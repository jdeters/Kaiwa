# Kaiwa 会話 — conversation practice within what you've learned

A Japanese conversation partner that only uses the vocabulary and grammar you
actually know. It reads your Anki decks and your textbook progress, and every
reply is checked against them before you see it.

- **Your vocabulary**: imported from Anki through AnkiConnect: the cards you've
  studied, plus every word from the textbook lessons you've finished.
- **Your grammar**: unlocked lesson by lesson from a textbook mapping
  (currently *Japanese for Busy People I*, Revised 4th Edition, Kana Version).
- **Checked replies**: Claude drafts each reply. A checker (UniDic morphology
  plus grammar rules) rejects anything outside what you know, and Claude
  rewrites it with specific feedback.
- **Kanji with furigana**: chat replies use normal kanji with readings above
  (corrected by the model where the dictionary misreads). Plain-text places
  without furigana (corrections, hints, reports) show unlearned kanji as kana,
  and kanji typing suggestions offer only kanji you've learned in Anki.

**Speaking progress**: every message you send is analyzed for which of your
known words and grammar you produced on your own (as opposed to echoing your
partner, using a hint, or with a mistake), typed or spoken. Progress → Speaking
shows what you've actually said and what you've studied but never used.

**Speaking practice**: each word and grammar point gets a speaking schedule
(using it on your own pushes it out; a mistake brings it back). Every
conversation gets a few targets: recent mistakes, items due, or things you've
never said. Your partner steers toward them without saying the target words
itself, and the session report shows which you hit. **Practice** goes a step further:
it designs a short scene around your targets and opens with the situation in
English (what you want to get across, never the Japanese), so the targets are
the natural thing to say.

Modes: free chat, 36 roleplay scenes plus custom scenes. Speak instead of
typing: tap the mic, talk, and when you pause your words land in the text box to
check and send (the reply is spoken back if auto-play is on). No
Japanese keyboard needed: type romaji and it becomes kana as you type, then
pick kanji for the words whose kanji you've learned (Tab or click), which is
also kanji recall practice. Also:
corrections of what you write, suggested replies (filtered to what you could
say), translations, tap-a-word lookups, session reports, and progress tracking.

## Setup

```bash
./setup.sh --with-voicevox                  # Python env, speech model, dictionary, voice engine
echo "ANTHROPIC_API_KEY=sk-ant-…" > .env     # or paste it in Settings later
```

Install [Anki](https://apps.ankiweb.net) with the
[AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on, keep Anki
open, and build your learner profile:

```bash
.venv/bin/python -m server.learner build
```

The first build creates `data/learner.yaml` from
[content/learner.example.yaml](content/learner.example.yaml). Edit it to match
your decks and the textbook lesson you've reached, then build again. Rebuild
whenever you've studied more.

```bash
./run.sh            # starts VOICEVOX + the app → http://localhost:8130
```

Voice: speech recognition is [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
running locally: the medium model by default (~3.5 s per reply on a laptop
CPU), given your partner's last line as context. `KAIWA_WHISPER_MODEL=small`
is about twice as fast but much less reliable on noisy or accented speech. The partner's voice is
the [VOICEVOX](https://voicevox.hiroshiba.jp/) engine, also local.
`--with-voicevox` downloads the Linux x64 build (~1.8 GB); on other platforms,
unzip the engine for your OS into `vendor/`.

## How it works

| Piece | Where |
|---|---|
| Grammar concepts (textbook-independent) | [content/grammar/concepts.yaml](content/grammar/concepts.yaml) |
| Textbook lesson → concept mapping | [content/textbooks/](content/textbooks/) |
| Anki import, learner profile | [server/learner/profile.py](server/learner/profile.py) |
| Grammar detection, checker | [server/learner/grammar.py](server/learner/grammar.py), [validator.py](server/learner/validator.py) |
| Draft → check → repair loop | [server/learner/generate.py](server/learner/generate.py) |
| Kana/kanji display | [server/learner/display.py](server/learner/display.py) |
| Speaking progress (what you produced) | [server/learner/production.py](server/learner/production.py) |
| Practice scheduling and targets | [server/learner/practice.py](server/learner/practice.py) |
| Web app | [server/main.py](server/main.py), [web/](web/) |

Command-line tools:

```bash
.venv/bin/python -m server.learner check "昨日、映画を見ました。"   # check a sentence
.venv/bin/python -m server.learner chat cafe -v                   # text chat in the terminal
.venv/bin/python -m server.learner eval                           # compliance/latency/cost evaluation
.venv/bin/python -m pytest tests/
```

## Your data

Sessions, corrections, and settings live in `data/kaiwa.db`; your learner
profile is `data/learner_profile.json`. Settings → Your data handles export,
import, and automatic backups. Your messages are sent to Anthropic's API to
generate replies.

## Credits & licenses

Built on [Kaiwa](https://github.com/yeshsanchez/Kaiwa) by yeshsanchez
(AGPL-3.0); this fork keeps that license.

- Dictionary data from [JMdict](https://www.edrdg.org/jmdict/j_jmdict.html)
  (via [jmdict-simplified](https://github.com/scriptin/jmdict-simplified)),
  property of the [EDRDG](https://www.edrdg.org/), used under
  [CC BY-SA 4.0](https://www.edrdg.org/edrdg/licence.html)
- Morphological analysis: [fugashi](https://github.com/polm/fugashi) + UniDic
- [VOICEVOX](https://voicevox.hiroshiba.jp/): generated audio must be credited
  per each voice library's terms (e.g. `VOICEVOX:四国めたん`)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (MIT), Whisper models by OpenAI (MIT)
- Romaji → kana typing: [WanaKana](https://github.com/WaniKani/WanaKana) (MIT)
- Icons: [Lucide](https://lucide.dev) (ISC) ·
  QR: [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) (MIT)

## License

[AGPL-3.0](LICENSE). If you run a modified version as a service, you must share
your changes.
