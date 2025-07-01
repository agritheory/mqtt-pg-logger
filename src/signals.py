from blinker import signal

alarm_refresh_signal = signal("refresh_alarms")
alarm_triggered = signal("alarm_triggered")
topic_signal = signal("topic")
