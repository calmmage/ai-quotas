# Custom Telegram reset markers

Telegram custom emojis are document IDs, not Unicode characters. Bot API HTML
uses `<tg-emoji emoji-id="ID">fallback</tg-emoji>`. System notifications may show
the fallback instead. The fallback must match the sticker's associated emoji.

The IDs below were resolved through `getCustomEmojiStickers` and `getStickerSet`
from the animated pack `RestrictedEmoji` (title **Animated Emoji**):

| Prior-period usage | Animated custom emoji | ID |
|---|---|---|
| ≥80% | green heart / 💚 fallback | `5449380056201697322` |
| 50–<80% | yellow heart / 💛 fallback | `5449366943666543715` |
| <50% | red heart / ❤️ fallback | `5449505950283078474` |

The pack was discovered from custom emoji IDs in the owner's messages. A bounded
primary-cache search since 1 July 2026 found 42 unique IDs across 25 messages,
including the owner's `petrlavrov` pixel pack. No message bodies enter the repo.
Private extraction evidence stays in ignored `output/playwright/`.

Enable per installation in the existing `config.toml`:

```toml
[telegram.reset_emoji]
green = "5449380056201697322"
yellow = "5449366943666543715"
red = "5449505950283078474"
```

Without this table, portable installs retain Unicode markers. Invalid IDs also
fall back. Existing weekly/monthly filtering and deduplication are unchanged.
The live bot accepted an edit to the existing reset notice and returned a
`custom_emoji` entity with the configured green-heart ID.

Bot eligibility must be checked per installation. Telegram currently permits
custom emoji for bots with an additional Fragment username, or direct messages
to private/group/supergroup chats when the bot's owner has Premium.

Sources checked 28 October 2026:

- [Bot API HTML formatting](https://core.telegram.org/bots/api#html-style)
- [Custom emoji stickers](https://core.telegram.org/bots/api#getcustomemojistickers)
- [MTProto custom emoji entities](https://core.telegram.org/api/custom-emoji)
