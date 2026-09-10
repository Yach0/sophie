---
title: Notes
icon: 📗
---
### Save and retrieve notes in chats

> If you want to save some frequently-used content in your chat, such as a FAQ, response templates, your favourite stickers or the whole interactive menu, you can do that with notes. \
> Notes allows saving different kind of content, from normal text messages to stickers and audio messages, notes also support adding inline message buttons.

## Available commands


### Commands

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/pmnotes` `/privatenotes` | - | Show current state of Private Notes | *Only in groups* |
| `/notes` `/saved` `/notelist` | `<?Search notes>` | Lists available notes. | *Disable-able* |
| `/get` | `<Note name>` `<?raw>` | Retrieve the note. |  |
{.card-view-on-mobile}

### Only admins

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/pmnotes` `/privatenotes` | `<New state>` | Control Private Notes | *Only in groups* |
| `/delnote` `/clear` | `<Note name>` | Deletes notes. |  |
| `/save` `/addnote` | `<Note names>` `<?Description>` `<Content>` | Save the note. |  |
| `/clearall` | - | Deletes all notes. |  |
{.card-view-on-mobile}
---
# Features


## AI notes generation
Please refer to the [AI help page](ai).

## Saving photos / stickers, adding buttons and fillings
Please refer to the [Saveables help page](/docs/Others/Saveables) of Sophie, as this information is global and work in many other places.

## Retrieving notes near Telegram's length limit

When retrieving a single note with `/get name` or `#name`, Sophie omits the note title and description if adding them would exceed Telegram's limit: 4096 characters for text messages or 1024 for a single media caption, after parsing HTML. The saved note content is kept intact; its title and description remain saved.

This title omission does not apply to multiple notes combined in one hashtag request. Retrieve those notes individually if the combined message is too long. Where long-text splitting is enabled (such as the fallback for rich messages), chunks are sent as plain text: literal `<`, `>` and `&` are preserved, but formatting and link targets are not.

## Note searching
Sophie implements 2 ways to search notes.
The simplest way is to filer by the note names using `/notes <filter>`.

Additionally, you can search by the content using `/search <content>`.

## PM Notes / Private Notes
By default, the notes are being shown in the group, but if you want to redirect users to the private messages of Sophie, you can enable private notes mode.
This would redirect users with a button to the PM, every time they request `/notes` or `/search`,

> Please note, that admins still can still access the `/notes` and other commands in the group directly.
> Additionally, users would still be able to `/get` the note.

# Advanced usage

### Multiple note names
Sophie supports setting many note names for the note, this helps users to retrieve the note by the expected note name.

For example
`/save pie | pierecipe | pie_recipes To cook the Pie you would need...`.
This would save the note with 3 different note names, and you can retrieve it by using any of them.

### Note Descriptions
Notes could have description that could help users indentify the note's content.
To add a description use following syntax:
`/saave pie "Tasty pie recipe"`. Of course, you can also add multiple note names, by just splitting them with `|`. See above for more information.
