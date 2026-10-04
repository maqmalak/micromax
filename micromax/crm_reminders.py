"""Moved to mm_core.crm_reminders (follow-up / calendar reminders and mail send-status bell notifications for
every site). The scheduler hook now lives in mm_core's hooks; this keeps old imports working."""

from mm_core.crm_reminders import *  # noqa: F401,F403
from mm_core.crm_reminders import _notify, cleanup_notifications_on_trash, send_due_reminders  # noqa: F401
