# Welcome Security expiry settings

`/welcomesecurity` offers expiry presets for pending captcha members. Changing expiry preserves whether captcha is enabled; it does not enable captcha or set the media restriction duration.

Each expiry button is bound to the originating database chat ID. It requires administrator access and the same current target chat. Switching the private-chat connection or disconnecting invalidates the old keyboard: reconnect to the intended group and reopen `/welcomesecurity`. Buttons sent before the chat-bound callback format was introduced must also be reopened; unbound payloads cannot update settings.
