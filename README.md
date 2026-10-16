# Token Voice Bot

Give your [Odin.fun](https://odin.fun) token a voice. A persona of your choice (Nikola Tesla, Confucius, a
grumpy pirate, or anyone you describe) posts original lines with a quote card to X and Telegram every few
hours. It runs on GitHub Actions in **your own** account, with **your own** keys.

Independent project, not affiliated with Odin.fun. Bots never talk about price or tell anyone to buy or sell.

## Set up your bot

The easiest way is the **Token Voices studio** (https://nikola-tesla-odin.github.io/), which signs you in with Odin, lets you pick or create a
voice, previews a post, and walks you through these steps:

1. **Use this template** (green button above) → create your own copy. Private is fine.
2. **Add `bot/persona.yaml`**, your token's voice and settings:
   ```yaml
   bot:
     every_hours: 4      # 2 to 24
     budget_usd: 1.5     # monthly cap on Claude spend; the bot stops itself when reached
     trending: false     # true = sometimes react to safe news (uses web search, costs more)
   name: Nikola Tesla
   # ... the rest of the persona (the studio writes this)
   ```
3. **Add repository secrets** (Settings → Secrets and variables → Actions):

   | Secret | Needed for |
   |---|---|
   | `ANTHROPIC_API_KEY` | Writing posts (console.anthropic.com) |
   | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram (bot from @BotFather, added as admin to your channel) |
   | `BUFFER_API_KEY` | X, via Buffer's free plan (publish.buffer.com/settings/api) |
   | `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | Quote cards on X (free Cloudinary account) |

4. **Actions → Token voice → Run workflow** to post once and check everything works.

After that it wakes every hour and posts when `every_hours` have passed. Each run takes under a minute,
well within GitHub's free minutes. Its memory (what it posted, and this month's spend) is kept on the
`bot-state` branch, so it doesn't repeat itself and never exceeds your budget.

**Pause:** Actions → Token voice → ⋯ → Disable workflow.

## Rules every bot follows

- No price talk, charts, predictions, or buy/sell/hold. Nothing it posts is financial advice.
- Never @-tags other accounts.
- Original lines only, never real quotations passed off as genuine.
- Stops itself at its monthly budget (Claude Haiku: about $0.0005–0.001 per post).

## Credits

Nikola Tesla portrait: Napoleon Sarony, c. 1890, public domain. Card font: Gelasio (SIL Open Font License).
