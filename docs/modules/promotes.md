## Administrator permission changes

If Telegram rejects `/demote` because the bot lacks the required administrator rights, Sophie reports the failure to the caller. It does not report a successful demotion or refresh the administrator cache as if the change succeeded.
