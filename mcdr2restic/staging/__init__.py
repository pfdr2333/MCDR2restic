# -*- coding: utf-8 -*-
"""[beta] 中转库（staging repository）实现。

先写入本地库，再由独立 worker 复制到远端库，缩短 save-off 冻结窗口。
本包所有行为都受 ``staging.enabled`` 控制；关闭时不得产生任何副作用。
"""

from __future__ import annotations
