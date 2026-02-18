from __future__ import annotations

from .models import Config, SetupError
from .ops.base_ops import BaseOps
from .ops.input_ops import InputOps
from .ops.state_ops import StateOps
from .ops.provision_ops import ProvisionOps
from .ops.dns_ops import DnsOps
from .ops.repo_ops import RepoOps
from .ops.deploy_ops import DeployOps
from .ops.destroy_ops import DestroyOps
from .ops.output_ops import OutputOps
from .ops.flow_ops import FlowOps


class SetupApp(
    BaseOps,
    InputOps,
    StateOps,
    ProvisionOps,
    DnsOps,
    RepoOps,
    DeployOps,
    DestroyOps,
    OutputOps,
    FlowOps,
):
    pass


__all__ = ["Config", "SetupApp", "SetupError"]
