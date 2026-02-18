from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path


class RepoOps:
    DEFAULT_RUBY_VERSION = "3.4.2"
    MIN_RUBY_MAJOR = 3
    MIN_RUBY_MINOR = 4
    MIN_RAILS_VERSION = "8.0"
    MIN_SQLITE_VERSION = "2.1"

    def record_local_repo_state(self, repo: Path, *, created_by_wizard: bool | None = None) -> None:
        self.cfg.local_repo_path = str(repo.resolve())
        if created_by_wizard is not None:
            self.cfg.local_repo_created_by_wizard = created_by_wizard
        if self.state is None:
            return
        cfg_state = self.state.setdefault("config", {})
        cfg_state["local_repo_path"] = str(repo.resolve())
        if created_by_wizard is not None:
            cfg_state["local_repo_created_by_wizard"] = bool(created_by_wizard)
        self.save_state()

    def preferred_ruby_version(self) -> str:
        explicit = (self.cfg.local_ruby_version or "").strip()
        if explicit:
            return explicit
        detected = self.detect_rbenv_active_ruby()
        if detected and self.is_supported_ruby_version(detected):
            return detected
        return self.DEFAULT_RUBY_VERSION

    def detect_rbenv_active_ruby(self) -> str:
        if not shutil.which("rbenv"):
            return ""
        proc = subprocess.run(
            ["bash", "-lc", 'eval "$(rbenv init - bash)" >/dev/null 2>&1; rbenv version-name'],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return ""
        out = (proc.stdout or "").strip()
        out = re.sub(r"^ruby-", "", out)
        if not out or out == "system":
            return ""
        if re.match(r"^\d+\.\d+(?:\.\d+)?$", out):
            return out
        return ""

    def is_supported_ruby_version(self, version: str) -> bool:
        match = re.match(r"^(\d+)\.(\d+)(?:\.\d+)?$", version)
        if not match:
            return False
        major = int(match.group(1))
        minor = int(match.group(2))
        return major > self.MIN_RUBY_MAJOR or (major == self.MIN_RUBY_MAJOR and minor >= self.MIN_RUBY_MINOR)

    def validate_preferred_ruby_version(self) -> None:
        version = self.preferred_ruby_version()
        if not re.match(r"^(\d+)\.(\d+)(?:\.\d+)?$", version):
            self.die(f"Invalid Ruby version format: {version} (expected e.g. 3.4.2)")
        if not self.is_supported_ruby_version(version):
            self.die(
                f"Ruby {version} is unsupported for upright (requires >= {self.MIN_RUBY_MAJOR}.{self.MIN_RUBY_MINOR}). "
                f"Use --local-ruby-version {self.DEFAULT_RUBY_VERSION} or newer."
            )

    def inferred_local_repo_dir(self) -> Path:
        image = (self.cfg.image_name or "").strip()
        tail = image.split("/")[-1] if image else "upright"
        repo_name = tail.split(":")[0] if ":" in tail else tail
        repo_name = repo_name or "upright"
        return (self.cwd / repo_name).resolve()

    def local_repo_dir(self) -> Path:
        raw = (self.cfg.local_repo_path or "").strip()
        if not raw:
            return self.inferred_local_repo_dir()
        return Path(raw).expanduser().resolve()

    def local_repo_display(self, repo: Path) -> str:
        try:
            return str(repo.relative_to(self.cwd))
        except ValueError:
            return str(repo)

    def repo_has_executable(self, repo: Path, relpath: str) -> bool:
        target = repo / relpath
        return target.exists() and os.access(target, os.X_OK)

    def run_in_repo(
        self,
        repo: Path,
        cmd: list[str],
        *,
        capture: bool = False,
        check: bool = True,
    ) -> str:
        proc = subprocess.run(
            cmd,
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and proc.returncode != 0:
            stderr = (proc.stderr or proc.stdout).strip() or f"command failed: {' '.join(cmd)}"
            self.die(stderr)
        if capture:
            return proc.stdout
        if proc.stdout:
            sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        return ""

    def is_rails_app(self, repo: Path) -> bool:
        return (repo / "Gemfile").exists() and self.repo_has_executable(repo, "bin/rails")

    def gemfile_has_upright(self, repo: Path) -> bool:
        gemfile = repo / "Gemfile"
        if not gemfile.exists():
            return False
        text = gemfile.read_text(encoding="utf-8", errors="replace")
        return re.search(r'^\s*gem\s+["\']upright["\']', text, re.MULTILINE) is not None

    def gemfile_has_gem(self, repo: Path, gem_name: str) -> bool:
        gemfile = repo / "Gemfile"
        if not gemfile.exists():
            return False
        text = gemfile.read_text(encoding="utf-8", errors="replace")
        pattern = rf'^\s*gem\s+["\']{re.escape(gem_name)}["\']'
        return re.search(pattern, text, re.MULTILINE) is not None

    def create_local_rails_app(self, repo: Path) -> None:
        if repo == self.cwd and (self.cwd / "scripts/stackscript/upright-bootstrap.sh").exists():
            self.die("Refusing to run rails new in setup-scripts repo. Set --local-repo-path to an app repo path.")
        if repo.exists() and any(repo.iterdir()):
            self.die(
                f"Local repo path exists but is not a Rails app: {self.local_repo_display(repo)}. "
                "Use an empty path, existing Rails repo, or --local-repo-url."
            )
        self.validate_preferred_ruby_version()
        self.ensure_rbenv()
        ruby_version = self.preferred_ruby_version()
        self.info(f"Creating Rails app at {self.local_repo_display(repo)}")
        if self.cfg.dry_run:
            return
        repo.parent.mkdir(parents=True, exist_ok=True)
        self.run_with_rbenv(
            repo.parent,
            (
                f"rbenv install -s {shlex.quote(ruby_version)}\n"
                "rbenv exec gem install bundler --no-document\n"
                f'rbenv exec gem install rails --no-document --version ">= {self.MIN_RAILS_VERSION}"\n'
                "rbenv rehash\n"
                f"DISABLE_SPRING=1 rbenv exec rails new {shlex.quote(repo.name)} --database=sqlite3 --skip-test"
            ),
            progress_label="rails new app scaffold",
        )
        self.record_local_repo_state(repo, created_by_wizard=True)

    def repo_has_git_commits(self, repo: Path) -> bool:
        if not (repo / ".git").exists():
            return False
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0

    def repo_looks_legacy_rails(self, repo: Path) -> bool:
        app_rb = repo / "config/application.rb"
        if app_rb.exists():
            text = app_rb.read_text(encoding="utf-8", errors="replace")
            if re.search(r"config\.load_defaults\s+[67](?:\.\d+)?", text):
                return True
        if (repo / "config/initializers/new_framework_defaults_6_1.rb").exists():
            return True
        for gem_name in ["webpacker", "turbolinks", "sass-rails"]:
            if self.gemfile_has_gem(repo, gem_name):
                return True
        return False

    def maybe_recreate_legacy_scratch_repo(self, repo: Path) -> None:
        if not repo.exists() or not self.is_rails_app(repo):
            return
        if self.repo_has_git_commits(repo):
            return
        if not self.repo_looks_legacy_rails(repo):
            return
        stamp = int(time.time())
        backup = repo.with_name(f"{repo.name}.legacy-backup.{stamp}")
        if self.cfg.dry_run:
            self.warn(
                f"DRY-RUN: would back up legacy scratch app {self.local_repo_display(repo)} "
                f"to {self.local_repo_display(backup)} and create fresh Rails {self.MIN_RAILS_VERSION}+ scaffold"
            )
            return
        self.warn(
            f"Detected legacy scratch Rails app at {self.local_repo_display(repo)}; "
            f"backing it up to {self.local_repo_display(backup)} and creating a fresh scaffold"
        )
        shutil.move(str(repo), str(backup))
        self.create_local_rails_app(repo)

    def ensure_rbenv(self) -> None:
        if shutil.which("rbenv"):
            self.info("Ruby toolchain: rbenv already installed")
            return
        if self.cfg.dry_run:
            self.info("DRY-RUN: would install rbenv + ruby-build")
            return
        if shutil.which("brew"):
            self.info("Ruby toolchain: installing rbenv + ruby-build via brew")
            self.run(["brew", "install", "rbenv", "ruby-build"])
            return
        if shutil.which("apt-get"):
            self.info("Ruby toolchain: installing rbenv + ruby-build via apt-get")
            self.run(["sudo", "apt-get", "update"])
            self.run(["sudo", "apt-get", "install", "-y", "rbenv", "ruby-build"])
            return
        self.die("rbenv not found. Install rbenv + ruby-build, then rerun.")

    def run_with_rbenv(self, repo: Path, script: str, *, progress_label: str | None = None) -> None:
        full_script = (
            "set -euo pipefail\n"
            'export RBENV_ROOT="${RBENV_ROOT:-$HOME/.rbenv}"\n'
            'export PATH="$RBENV_ROOT/bin:$PATH"\n'
            'eval "$(rbenv init - bash)"\n'
            f"cd {shlex.quote(str(repo))}\n"
            f"{script}\n"
        )
        if not progress_label:
            self.shell(full_script)
            return
        proc = subprocess.Popen(["bash", "-lc", full_script], cwd=self.cwd)
        start = time.monotonic()
        last_log = 0.0
        while True:
            rc = proc.poll()
            now = time.monotonic()
            elapsed = int(now - start)
            if rc is not None:
                if rc != 0:
                    self.die(f"{progress_label} failed (exit {rc})")
                self.info(f"{progress_label} complete ({elapsed}s)")
                return
            if now - last_log >= 10:
                self.info(f"{progress_label} in progress... ({elapsed}s)")
                last_log = now
            time.sleep(0.5)

    def ensure_rails_dependency(self, repo: Path) -> None:
        min_rails = self.MIN_RAILS_VERSION
        if self.cfg.dry_run:
            self.info(
                f"DRY-RUN: would ensure Rails >= {min_rails} in {self.local_repo_display(repo)}"
            )
            return
        self.info(f"Ensuring Rails >= {min_rails} in {self.local_repo_display(repo)}")
        gemfile = repo / "Gemfile"
        if not gemfile.exists():
            self.run_with_rbenv(
                repo,
                f'rbenv exec bundle add rails --version ">= {min_rails}"',
                progress_label="bundle add rails",
            )
            return

        lines = gemfile.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        rails_re = re.compile(r'^(\s*)gem\s+["\']rails["\'](?:\s*,.*)?$')
        first_rails_idx = -1
        updated: list[str] = []
        removed_extra = 0
        for i, line in enumerate(lines):
            if rails_re.match(line):
                if first_rails_idx == -1:
                    first_rails_idx = i
                    indent = rails_re.match(line).group(1) if rails_re.match(line) else ""
                    updated.append(f'{indent}gem "rails", ">= {min_rails}"\n')
                else:
                    removed_extra += 1
                continue
            updated.append(line)

        if first_rails_idx == -1:
            self.info("Gemfile has no explicit rails gem; adding rails requirement")
            self.run_with_rbenv(
                repo,
                f'rbenv exec bundle add rails --version ">= {min_rails}"',
                progress_label="bundle add rails",
            )
        else:
            if updated != lines:
                gemfile.write_text("".join(updated), encoding="utf-8")
            if removed_extra > 0:
                self.warn(f"Removed {removed_extra} duplicate rails gem entries from Gemfile")
            self.info("Updated Gemfile rails requirement to >= 8.0")
            self.run_with_rbenv(repo, "rbenv exec bundle update rails", progress_label="bundle update rails")

    def ensure_puma_dependency(self, repo: Path) -> None:
        min_puma = "6.0"
        if self.cfg.dry_run:
            self.info(
                f"DRY-RUN: would ensure puma >= {min_puma} in {self.local_repo_display(repo)}"
            )
            return
        self.info(f"Ensuring puma >= {min_puma} in {self.local_repo_display(repo)}")
        gemfile = repo / "Gemfile"
        if not gemfile.exists():
            self.run_with_rbenv(
                repo,
                f'rbenv exec bundle add puma --version ">= {min_puma}"',
                progress_label="bundle add puma",
            )
            return

        lines = gemfile.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        puma_re = re.compile(r'^(\s*)gem\s+["\']puma["\'](?:\s*,.*)?$')
        first_puma_idx = -1
        updated: list[str] = []
        removed_extra = 0
        for i, line in enumerate(lines):
            if puma_re.match(line):
                if first_puma_idx == -1:
                    first_puma_idx = i
                    indent = puma_re.match(line).group(1) if puma_re.match(line) else ""
                    updated.append(f'{indent}gem "puma", ">= {min_puma}"\n')
                else:
                    removed_extra += 1
                continue
            updated.append(line)

        if first_puma_idx == -1:
            self.info("Gemfile has no explicit puma gem; adding puma requirement")
            self.run_with_rbenv(
                repo,
                f'rbenv exec bundle add puma --version ">= {min_puma}"',
                progress_label="bundle add puma",
            )
        else:
            if updated != lines:
                gemfile.write_text("".join(updated), encoding="utf-8")
            if removed_extra > 0:
                self.warn(f"Removed {removed_extra} duplicate puma gem entries from Gemfile")
            self.info("Updated Gemfile puma requirement to >= 6.0")
            self.run_with_rbenv(repo, "rbenv exec bundle update puma", progress_label="bundle update puma")

    def ensure_sqlite_dependency(self, repo: Path) -> None:
        min_sqlite = self.MIN_SQLITE_VERSION
        if self.cfg.dry_run:
            self.info(
                f"DRY-RUN: would ensure sqlite3 >= {min_sqlite} in {self.local_repo_display(repo)}"
            )
            return
        self.info(f"Ensuring sqlite3 >= {min_sqlite} in {self.local_repo_display(repo)}")
        gemfile = repo / "Gemfile"
        if not gemfile.exists():
            self.run_with_rbenv(
                repo,
                f'rbenv exec bundle add sqlite3 --version ">= {min_sqlite}"',
                progress_label="bundle add sqlite3",
            )
            return

        lines = gemfile.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        sqlite_re = re.compile(r'^(\s*)gem\s+["\']sqlite3["\'](?:\s*,.*)?$')
        first_sqlite_idx = -1
        updated: list[str] = []
        removed_extra = 0
        for i, line in enumerate(lines):
            if sqlite_re.match(line):
                if first_sqlite_idx == -1:
                    first_sqlite_idx = i
                    indent = sqlite_re.match(line).group(1) if sqlite_re.match(line) else ""
                    updated.append(f'{indent}gem "sqlite3", ">= {min_sqlite}"\n')
                else:
                    removed_extra += 1
                continue
            updated.append(line)

        if first_sqlite_idx == -1:
            self.info("Gemfile has no explicit sqlite3 gem; adding sqlite3 requirement")
            self.run_with_rbenv(
                repo,
                f'rbenv exec bundle add sqlite3 --version ">= {min_sqlite}"',
                progress_label="bundle add sqlite3",
            )
        else:
            if updated != lines:
                gemfile.write_text("".join(updated), encoding="utf-8")
            if removed_extra > 0:
                self.warn(f"Removed {removed_extra} duplicate sqlite3 gem entries from Gemfile")
            self.info("Updated Gemfile sqlite3 requirement to >= 2.1")
            self.run_with_rbenv(repo, "rbenv exec bundle update sqlite3", progress_label="bundle update sqlite3")

    def remove_legacy_rails_gems(self, repo: Path) -> None:
        legacy = [g for g in ["sass-rails", "webpacker", "turbolinks"] if self.gemfile_has_gem(repo, g)]
        if not legacy:
            return
        gems = " ".join(legacy)
        if self.cfg.dry_run:
            self.info(
                f"DRY-RUN: would remove legacy Rails gems incompatible with Rails 8 in "
                f"{self.local_repo_display(repo)}: {gems}"
            )
            return
        self.info(f"Removing legacy Rails gems incompatible with Rails 8: {gems}")
        self.run_with_rbenv(
            repo,
            f"rbenv exec bundle remove {gems}",
            progress_label="bundle remove legacy Rails gems",
        )

    def ensure_bundle_install(self, repo: Path, reason: str) -> None:
        if self.cfg.dry_run:
            self.info(f"DRY-RUN: would run bundle install ({reason}) in {self.local_repo_display(repo)}")
            return
        self.info(f"Running bundle install ({reason}) in {self.local_repo_display(repo)} (can take several minutes)")
        self.run_with_rbenv(repo, "rbenv exec bundle install", progress_label=f"bundle install ({reason})")

    def ensure_gemfile_ruby_version(self, repo: Path) -> None:
        gemfile = repo / "Gemfile"
        if not gemfile.exists():
            return
        desired = self.preferred_ruby_version()
        lines = gemfile.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        ruby_re = re.compile(r'^(\s*)ruby\s+["\']([^"\']+)["\'](.*)$')
        changed = False
        found = False
        updated: list[str] = []
        for line in lines:
            m = ruby_re.match(line)
            if m:
                found = True
                current = m.group(2).strip()
                if current != desired:
                    updated.append(f'{m.group(1)}ruby "{desired}"{m.group(3)}\n')
                    changed = True
                    self.warn(
                        f"Gemfile ruby version {current} is incompatible with bootstrap Ruby {desired}; updating Gemfile"
                    )
                else:
                    updated.append(line)
                continue
            updated.append(line)
        if not found:
            return
        if changed and not self.cfg.dry_run:
            gemfile.write_text("".join(updated), encoding="utf-8")
            self.info(f"Updated Gemfile ruby version to {desired}")

    def ensure_rails_boot_file(self, repo: Path) -> None:
        boot = repo / "config/boot.rb"
        if boot.exists():
            return
        if self.cfg.dry_run:
            self.warn(f"DRY-RUN: would create missing Rails boot file at {self.local_repo_display(boot)}")
            return
        boot.parent.mkdir(parents=True, exist_ok=True)
        boot.write_text(
            (
                'ENV["BUNDLE_GEMFILE"] ||= File.expand_path("../Gemfile", __dir__)\n\n'
                'require "bundler/setup"\n'
                'require "bootsnap/setup"\n'
            ),
            encoding="utf-8",
        )
        self.warn(f"Created missing Rails boot file: {self.local_repo_display(boot)}")

    def ensure_database_yml(self, repo: Path) -> None:
        database_yml = repo / "config/database.yml"
        if database_yml.exists():
            return
        if self.cfg.dry_run:
            self.warn(f"DRY-RUN: would create missing database config at {self.local_repo_display(database_yml)}")
            return
        database_yml.parent.mkdir(parents=True, exist_ok=True)
        database_yml.write_text(
            (
                "default: &default\n"
                "  adapter: sqlite3\n"
                "  pool: <%= ENV.fetch(\"RAILS_MAX_THREADS\") { 5 } %>\n"
                "  timeout: 5000\n\n"
                "development:\n"
                "  <<: *default\n"
                "  database: db/development.sqlite3\n\n"
                "test:\n"
                "  <<: *default\n"
                "  database: db/test.sqlite3\n\n"
                "production:\n"
                "  <<: *default\n"
                "  database: db/production.sqlite3\n"
            ),
            encoding="utf-8",
        )
        self.warn(f"Created missing database config: {self.local_repo_display(database_yml)}")

    def ensure_js_entrypoint(self, repo: Path) -> None:
        app_js = repo / "app/javascript/application.js"
        if app_js.exists():
            return
        if self.cfg.dry_run:
            self.warn(f"DRY-RUN: would create missing JavaScript entrypoint at {self.local_repo_display(app_js)}")
            return
        app_js.parent.mkdir(parents=True, exist_ok=True)
        app_js.write_text("// Entry point required by modern Rails/Upright install tasks.\n", encoding="utf-8")
        self.warn(f"Created missing JavaScript entrypoint: {self.local_repo_display(app_js)}")

    def upright_install_already_applied(self, repo: Path) -> bool:
        db_migrate = repo / "db/migrate"
        if db_migrate.exists() and any(db_migrate.glob("*upright*.rb")):
            return True
        for path in [
            repo / "config/initializers/upright.rb",
            repo / "config/upright.yml",
        ]:
            if path.exists():
                return True
        return False

    def ensure_upright_generator(self, repo: Path) -> None:
        if self.upright_install_already_applied(repo):
            self.info(f"upright:install already present in {self.local_repo_display(repo)}; skipping generator")
            return
        if self.cfg.dry_run:
            self.info(f"DRY-RUN: would run upright:install generator in {self.local_repo_display(repo)}")
            return
        self.info(f"Running upright:install generator in {self.local_repo_display(repo)}")
        self.run_with_rbenv(
            repo,
            "DISABLE_SPRING=1 rbenv exec bundle exec rails generate upright:install",
            progress_label="rails generate upright:install",
        )

    def ensure_ruby_toolchain(self, repo: Path) -> None:
        self.ensure_rbenv()
        ruby_version = self.preferred_ruby_version()
        if self.cfg.dry_run:
            self.info(f"DRY-RUN: would ensure Ruby {ruby_version} via rbenv in {self.local_repo_display(repo)}")
            return
        self.info(f"Ruby toolchain: ensuring Ruby {ruby_version} via rbenv in {self.local_repo_display(repo)}")
        self.run_with_rbenv(
            repo,
            f"rbenv install -s {shlex.quote(ruby_version)}\n"
            f"rbenv local {shlex.quote(ruby_version)}\n"
            "rbenv exec gem install bundler --no-document\n"
            "rbenv rehash",
            progress_label="ruby toolchain setup",
        )

    def ensure_local_app_repo(self) -> Path:
        repo = self.local_repo_dir()
        self.record_local_repo_state(repo)
        want_bootstrap = self.cfg.bootstrap_local_app or bool((self.cfg.local_repo_url or "").strip())
        self.info(f"Local app repo target: {self.local_repo_display(repo)}")
        if self.cfg.bootstrap_local_app:
            self.info(
                "Local app bootstrap: enabled "
                "(Ruby + Rails>=8 + Upright gem + upright:install + Rails db:migrate)"
            )
        else:
            self.info("Local app bootstrap: disabled")
        if repo == self.cwd and (self.cwd / "scripts/stackscript/upright-bootstrap.sh").exists():
            self.die(
                "Refusing to use setup-scripts repo as local app repo. "
                "Set --local-repo-path to your Rails app path."
            )

        if not repo.exists():
            if self.cfg.local_repo_url:
                self.check_dependency("git")
                self.info(f"Cloning app repo to {self.local_repo_display(repo)}")
                if not self.cfg.dry_run:
                    repo.parent.mkdir(parents=True, exist_ok=True)
                    self.run(["git", "clone", self.cfg.local_repo_url, str(repo)])
            elif self.cfg.bootstrap_local_app:
                self.create_local_rails_app(repo)
            elif repo != self.cwd:
                self.die(
                    f"Local repo path does not exist: {self.local_repo_display(repo)}. "
                    "Set --local-repo-url or --bootstrap-local-app."
                )

        if self.cfg.dry_run and not repo.exists():
            return repo

        self.maybe_recreate_legacy_scratch_repo(repo)

        if self.cfg.bootstrap_local_app:
            if not self.is_rails_app(repo):
                self.create_local_rails_app(repo)
            else:
                self.info(f"Rails app detected at {self.local_repo_display(repo)}")

            self.validate_preferred_ruby_version()
            self.ensure_ruby_toolchain(repo)
            if self.cfg.dry_run:
                self.info(
                    f"DRY-RUN: would ensure Rails >= {self.MIN_RAILS_VERSION}, upright gem, and db:prepare "
                    f"in {self.local_repo_display(repo)}"
                )
                return repo

            self.ensure_gemfile_ruby_version(repo)
            self.ensure_rails_boot_file(repo)
            self.ensure_database_yml(repo)
            self.ensure_js_entrypoint(repo)
            self.ensure_bundle_install(repo, "bootstrap")
            self.ensure_rails_dependency(repo)
            self.ensure_puma_dependency(repo)
            self.ensure_sqlite_dependency(repo)
            self.remove_legacy_rails_gems(repo)
            if not self.gemfile_has_upright(repo):
                self.info(f"Adding upright gem in {self.local_repo_display(repo)}")
                self.run_with_rbenv(repo, "rbenv exec bundle add upright", progress_label="bundle add upright")
            else:
                self.info(f"Upright gem already present in {self.local_repo_display(repo)}")
            self.ensure_bundle_install(repo, "post-upright")
            self.ensure_upright_generator(repo)
            self.info(f"Running Rails db:prepare in {self.local_repo_display(repo)}")
            self.run_with_rbenv(
                repo,
                "DISABLE_SPRING=1 rbenv exec bundle exec rails db:prepare",
                progress_label="rails db:prepare",
            )
            self.info(f"Running Rails db:migrate in {self.local_repo_display(repo)}")
            self.run_with_rbenv(
                repo,
                "DISABLE_SPRING=1 rbenv exec bundle exec rails db:migrate",
                progress_label="rails db:migrate",
            )

        elif want_bootstrap and not self.is_rails_app(repo):
            self.die(
                f"Local repo is not a Rails app: {self.local_repo_display(repo)}. "
                "Use --bootstrap-local-app or point to an existing Rails repo."
            )

        return repo
