---
title: Locks
icon: 🔓
---
### Lock specific message types in chats

> Allows administrators to lock specific types of messages in chats. \
> Prevents users from sending certain content types like stickers, GIFs, URLs, sticker packs, and languages.

## Available commands


### Commands

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/lockable` `/locktypes` | - | Shows all lockable message types | *Disable-able* |
| `/locklanguages` `/locklangs` | - | Shows all supported languages for locking | *Disable-able* |
{.card-view-on-mobile}

### Only admins

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/lock` | `<Lock type>` | Lock a message type in the chat | *Disable-able* |
| `/unlock` | `<Lock type>` | Unlock a message type in the chat | *Disable-able* |
| `/locksticker` | - | Lock a sticker pack in the chat | *Disable-able* |
| `/locks` `/locked` | - | Show currently locked message types in the chat | *Disable-able* |
| `/unlockall` | - | - |  |
{.card-view-on-mobile}
---

## Lockable types

The list below is generated from the same source used by /lockable.

#### Media types


- **`audio`**: Messages with audio files
- **`document`**: Messages with files/documents
- **`gif`**: Messages with GIF animations
- **`photo`**: Messages with photos
- **`video`**: Messages with videos
- **`videonote`**: Messages with video notes (round videos)
- **`voice`**: Messages with voice recordings
- **`sticker`**: Messages with stickers
- **`contact`**: Messages with shared contacts
- **`location`**: Messages with location or venue
- **`poll`**: Messages with polls or quizzes
- **`game`**: Messages with Telegram games
- **`text`**: Text-only messages without media
- **`dice`**: Messages with dice rolls
- **`checklist`**: Messages with checklists
- **`album`**: Messages with media albums (grouped photos/videos)

#### Entities and links


- **`url`**: URL entities in messages
- **`email`**: Email address entities
- **`phone`**: Phone number entities
- **`cashtag`**: Cashtag entities ($TICKER)
- **`invitelink`**: Telegram invite links (t.me/+)
- **`mention`**: Mention entities (@username)
- **`botlink`**: Links to Telegram bots (t.me/botname)
- **`command`**: Bot command entities (/command@bot)
- **`spoiler`**: Spoiler text entities
- **`emoji`**: Messages containing emoji
- **`emojicustom`**: Custom emoji entities
- **`emojigame`**: Game messages with emoji
- **`emojionly`**: Messages containing only emoji
- **`button`**: Messages with inline keyboard buttons
- **`hashtag`**: Hashtag entities (#tag)
- **`code`**: Inline code entities (`code`)
- **`pre`**: Preformatted code blocks
- **`blockquote`**: Blockquote entities
- **`underline`**: Underlined text entities
- **`strikethrough`**: Strikethrough text entities
- **`webpreview`**: Messages with web page link previews

#### Forwards


- **`forward`**: All forwarded messages
- **`forwardbot`**: Messages forwarded from bots
- **`forwardchannel`**: Messages forwarded from channels
- **`forwardstory`**: Messages forwarded from stories
- **`forwarduser`**: Messages forwarded from users
- **`externalreply`**: External reply references

#### Text patterns


- **`cjk`**: Messages containing CJK characters (Chinese, Japanese, Korean)
- **`cyrillic`**: Messages containing Cyrillic characters
- **`rtl`**: Messages containing RTL (right-to-left) text
- **`arabic`**: Messages containing Arabic script
- **`zalgo`**: Messages with excessive formatting characters (glitch text)

#### Sticker types


- **`stickeranimated`**: Animated stickers
- **`stickerpremium`**: Premium animated stickers

- **`stickerpack:PACK_ID`**: Lock a specific sticker pack by its ID

#### Languages


- **`language:af`**: Afrikaans
- **`language:ar`**: Arabic
- **`language:az`**: Azerbaijani
- **`language:be`**: Belarusian
- **`language:bg`**: Bulgarian
- **`language:bn`**: Bengali
- **`language:bs`**: Bosnian
- **`language:ca`**: Catalan
- **`language:cs`**: Czech
- **`language:cy`**: Welsh
- **`language:da`**: Danish
- **`language:de`**: German
- **`language:el`**: Greek
- **`language:en`**: English
- **`language:eo`**: Esperanto
- **`language:es`**: Spanish
- **`language:et`**: Estonian
- **`language:eu`**: Basque
- **`language:fa`**: Persian
- **`language:fi`**: Finnish
- **`language:fr`**: French
- **`language:ga`**: Irish
- **`language:gu`**: Gujarati
- **`language:he`**: Hebrew
- **`language:hi`**: Hindi
- **`language:hr`**: Croatian
- **`language:hu`**: Hungarian
- **`language:hy`**: Armenian
- **`language:id`**: Indonesian
- **`language:is`**: Icelandic
- **`language:it`**: Italian
- **`language:ja`**: Japanese
- **`language:ka`**: Georgian
- **`language:kk`**: Kazakh
- **`language:ko`**: Korean
- **`language:la`**: Latin
- **`language:lg`**: Ganda
- **`language:lt`**: Lithuanian
- **`language:lv`**: Latvian
- **`language:mi`**: Maori
- **`language:mk`**: Macedonian
- **`language:mn`**: Mongolian
- **`language:mr`**: Marathi
- **`language:ms`**: Malay
- **`language:nb`**: Bokmal
- **`language:nl`**: Dutch
- **`language:nn`**: Nynorsk
- **`language:pa`**: Punjabi
- **`language:pl`**: Polish
- **`language:pt`**: Portuguese
- **`language:ro`**: Romanian
- **`language:ru`**: Russian
- **`language:sk`**: Slovak
- **`language:sl`**: Slovene
- **`language:sn`**: Shona
- **`language:so`**: Somali
- **`language:sq`**: Albanian
- **`language:sr`**: Serbian
- **`language:st`**: Sotho
- **`language:sv`**: Swedish
- **`language:sw`**: Swahili
- **`language:ta`**: Tamil
- **`language:te`**: Telugu
- **`language:th`**: Thai
- **`language:tl`**: Tagalog
- **`language:tn`**: Tswana
- **`language:tr`**: Turkish
- **`language:ts`**: Tsonga
- **`language:uk`**: Ukrainian
- **`language:ur`**: Urdu
- **`language:vi`**: Vietnamese
- **`language:xh`**: Xhosa
- **`language:yo`**: Yoruba
- **`language:zh`**: Chinese
- **`language:zu`**: Zulu

#### Special


- **`all`**: Blocks all message types
- **`bot`**: Messages from bot accounts
- **`anonchannel`**: Messages sent on behalf of a channel anonymously
- **`comment`**: Messages sent as comments in channels
- **`guestbot`**: Messages from guest bots (bots mentioned via @username without being chat members)
- **`inline`**: Messages sent via inline bots
- **`outsidereaction`**: Reactions from users who are not members of the chat
- **`media`**: Any media message (photo, video, audio, document, sticker, etc.)
- **`edited`**: Edited messages
---
## Need help choosing?

If you are not sure whether your case should use a lock type, a text matcher, regex, or an AI filter,
use `/aiaddfilter` first.

Example:

```
/aiaddfilter block crypto spam
```

Sophie will suggest matching handlers for you.
You can then pick the best one and create the real filter with `/addfilter <handler>`.
