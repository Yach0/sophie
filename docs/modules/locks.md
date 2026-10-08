## Need help choosing?

If you are not sure whether your case should use a lock type, a text matcher, regex, or an AI filter,
use `/aiaddfilter` first.

Example:

```
/aiaddfilter block crypto spam
```

Sophie will suggest matching handlers for you.
You can then pick the best one and create the real filter with `/addfilter <handler>`.

## Rich message text

Language and text-pattern locks read formatted rich text, including nested inline formatting and table cells. Rich text takes precedence over a plain-text fallback; ordinary message text and captions remain supported.

The `text` lock also recognizes rich text-only messages. Captions alone, media messages, and rich messages containing media blocks are exempt from this lock. Entity-based locks continue to use Telegram's message/caption entities and their original text offsets.

For locks, the shared extractor reads a block's text or table cells. Text inside container blocks (such as lists, details, and block quotations) and rich media captions is not yet extracted by locks; AI reply context retains its full visible-text projection. Rich formatting and media blocks are not converted into legacy Telegram entities or media fields by this fix.
