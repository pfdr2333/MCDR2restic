# -*- coding: utf-8 -*-
"""[beta] 中转库失败分类：复制与仓库探测共用一套判定。

分类只认两类输入：插件自己产生的 ``i18n_key`` 与 restic 的原始英文输出。
**绝不对本地化文案做子串匹配**——中文译文命中英文关键词纯属巧合，会让
"配额不足"和"网络超时"在不同语言下被归到相反类别，进而错停或错续重试。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from mcdr2restic.core.models import BackupProblem, ResticCommandResult, TextEnum
from mcdr2restic.restic.restic_result import combine_restic_output


class FailureKind(TextEnum):
    AUTH = "auth"
    QUOTA = "quota"
    MISSING = "missing"
    UNREACHABLE = "unreachable"


# 鉴权签名：口令错误与对象存储的签名/凭据错误都停止自动重试
AUTH_ERROR_MARKERS = (
    "wrong password",
    "password is incorrect",
    "invalid password",
    "no key found",
    "authentication",
    "unauthorized",
    "forbidden",
    "access denied",
    "request signature",
    "signaturedoesnotmatch",
    "invalidaccesskeyid",
    "invalid token",
)
# 配额签名只保留能指向"空间/配额"的组合词：裸 "exceeded" 会命中
# "context deadline exceeded" 这类超时错误，把网络故障误判成配额耗尽
QUOTA_ERROR_MARKERS = (
    "quota",
    "quota exceeded",
    "insufficient storage",
    "insufficientstorage",
    "entity too large",
    "entitytoolarge",
    "storage limit",
    "storagefull",
    "out of space",
    "no space left",
)
MISSING_REPOSITORY_MARKERS = (
    "repository does not exist",
    "no such file or directory",
    "unable to open config file",
    "is there a repository at the following location",
    "does not exist",
)

# 插件自己就能定性的失败：不依赖任何译文或 restic 输出
I18N_FAILURE_KINDS: Dict[str, FailureKind] = {
    "error.staging.remote_auth": FailureKind.AUTH,
    "error.staging.local_password": FailureKind.AUTH,
    "error.staging.remote_quota": FailureKind.QUOTA,
    "error.staging.remote_unreachable": FailureKind.UNREACHABLE,
    "error.staging.remote_unavailable_for_init": FailureKind.UNREACHABLE,
    "error.restic.timeout": FailureKind.UNREACHABLE,
    "error.restic.deadline_exhausted": FailureKind.UNREACHABLE,
    "error.restic.start_failed": FailureKind.UNREACHABLE,
    "error.restic.executable_not_found": FailureKind.UNREACHABLE,
}


def classify_failure(error: Any) -> FailureKind:
    """[beta] 把 restic 结果或插件异常归为可重试/不可重试的单一类别。

    ``i18n_key`` 优先；其次只匹配原始 restic 输出。拿不到原始输出时按
    UNREACHABLE 处理（可重试），宁可多试一次也不误停自动重试。
    """

    kind = I18N_FAILURE_KINDS.get(_i18n_key(error))
    if kind is not None:
        return kind
    text = raw_failure_text(error)
    if text is None:
        return FailureKind.UNREACHABLE
    return classify_failure_text(text)


def classify_failure_text(text: str) -> FailureKind:
    lowered = str(text or "").lower()
    if any(marker in lowered for marker in AUTH_ERROR_MARKERS):
        return FailureKind.AUTH
    if any(marker in lowered for marker in QUOTA_ERROR_MARKERS):
        return FailureKind.QUOTA
    if any(marker in lowered for marker in MISSING_REPOSITORY_MARKERS):
        return FailureKind.MISSING
    return FailureKind.UNREACHABLE


def raw_failure_text(error: Any) -> Optional[str]:
    """取未翻译的原始失败输出；拿不到时返回 None，禁止回退到译文。"""

    if isinstance(error, ResticCommandResult):
        return combine_restic_output(error)
    params = getattr(error, "i18n_params", None)
    if isinstance(params, dict):
        output = params.get("output")
        if output:
            return str(output)
    if isinstance(error, BackupProblem):
        # 插件异常的内容可能已是本地化文案，绝不能拿来做关键词匹配
        return None
    return str(error)


def _i18n_key(error: Any) -> str:
    return str(getattr(error, "i18n_key", "") or "")


__all__ = [
    "AUTH_ERROR_MARKERS",
    "I18N_FAILURE_KINDS",
    "MISSING_REPOSITORY_MARKERS",
    "QUOTA_ERROR_MARKERS",
    "FailureKind",
    "classify_failure",
    "classify_failure_text",
    "raw_failure_text",
]
