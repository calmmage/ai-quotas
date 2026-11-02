# Custom Telegram reset markers

Each reset line can draw a fresh custom emoji uniformly from its colour's large
bundled catalogue. There is no account seed, daily assignment, or short rotation.
Independent random draws can occasionally repeat.

Enable it in the existing `~/.config/ai-quotas/config.toml` (or `AI_QUOTAS_CONFIG`):

```toml
[telegram.reset_emoji]
green = "random"
yellow = "random"
red = "random"
```

This owner's installation uses this configuration. The package includes
[the verified catalogue](../ai_quotas/reset_emoji_pool.json), so choosing an emoji
does not make a Telegram request and does not require a personal-account session.

| Prior-period usage | Colour | Custom emojis |
|---|---|---|
| ≥80% | green | 163 |
| 50–<80% | yellow | 138 |
| <50% | red | 182 |

The 483 document IDs span 333 packs and were verified on 29 October 2026:

- Discovered globally with Telegram's `messages.searchStickers`, searching by colour.
- Excluded assets that Telegram recolours to the surrounding text (`text_color` / `needs_repainting`).
- Checked thumbnail hue and inspected contact sheets; removed blank assets and fragments of multi-emoji labels.
- Checked five frames of each TGS animation and every fifth frame of each video; removed assets that switched to a different dominant colour. This is sampled visual verification, not a proof of every animation frame.
- Resolved every retained ID through the existing bot's `getCustomEmojiStickers`; retained each asset's exact associated Unicode fallback and source pack.

Telegram HTML uses `<tg-emoji emoji-id="ID">fallback</tg-emoji>`. Notifications or
clients without custom-emoji support may show that associated Unicode fallback,
which need not have the same colour as the custom artwork. The bot's delivery
report records the exact randomly selected tags sent in each batch.

Without this table, portable installs retain Unicode circles. A numeric string
still selects the old fixed asset, for compatibility. The original verified
`RestrictedEmoji` heart IDs are green `5449380056201697322`, yellow
`5449366943666543715`, and red `5449505950283078474`. Invalid settings or an
unavailable catalogue fall back to Unicode circles. Account/window eligibility,
colour thresholds and delivery deduplication are unchanged.

Bot eligibility must be checked per installation. Telegram permits custom emoji
for bots with an additional Fragment username, or direct messages to private,
group and supergroup chats when the bot's owner has Premium. Earlier delivery
established this bot's eligibility; this change does not send artificial alerts.

See the [walkthrough and verification](walkthroughs/2026-10-29-random-reset-emojis.md).

Sources checked 29 October 2026:

- [Global emoji search](https://core.telegram.org/method/messages.searchStickers)
- [Bot API HTML formatting](https://core.telegram.org/bots/api#html-style)
- [Custom emoji stickers](https://core.telegram.org/bots/api#getcustomemojistickers)
- [Custom emoji fallback and recolouring](https://core.telegram.org/api/custom-emoji)
