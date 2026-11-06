# -*- coding: utf-8 -*-
from __future__ import annotations

from mcdreforged.api.all import CommandSource

from mcdr2restic.commands.command_context import CommandContext
from mcdr2restic.config.config_loader import get_config_snapshot
from mcdr2restic.core.i18n import make_source_translate
from mcdr2restic.notifications.notification_dispatcher import NotificationDispatcher

CHANNEL_LABEL_KEYS = {
    "onebot": "label.notify.channel.onebot",
    "discord": "label.notify.channel.discord",
    "custom_webhook": "label.notify.channel.custom_webhook",
}

CHANNEL_STATUS_KEYS = {
    "sent": "info.notify.test.sent",
    "disabled": "info.notify.test.disabled",
    "not_started": "info.notify.test.not_started",
    "failed": "info.notify.test.failed",
}


class NotifyCommands:
    def __init__(self, context: CommandContext):
        self.context = context

    def command_notify_test(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        dispatcher = NotificationDispatcher(self.context.app_runtime, lambda: cfg)
        translate = make_source_translate(source, server)
        lines = [translate("info.notify.test_header")]
        for result in dispatcher.send_test_notification(cfg):
            channel = translate(
                CHANNEL_LABEL_KEYS.get(result.section, result.section)
            )
            status = translate(
                CHANNEL_STATUS_KEYS.get(result.status, result.status),
                error=result.error or "",
            )
            lines.append(translate("info.notify.test_line", channel=channel, result=status))
        source.reply("\n".join(lines))
