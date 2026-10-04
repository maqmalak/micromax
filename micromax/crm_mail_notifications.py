"""Moved to mm_core.crm_mail_notifications (inbound-email bell notifications for every site). The doc_events
hook now lives in mm_core's hooks; this keeps old imports working."""

from mm_core.crm_mail_notifications import on_communication_after_insert  # noqa: F401
