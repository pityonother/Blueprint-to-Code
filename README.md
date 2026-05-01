# Petal Walk

`Petal Walk` is a Phaser 3 + TypeScript + Vite narrative walking prototype for the browser.

## Run

```bash
npm install
npm run dev
```

Open the local Vite URL in a browser.

## Controls

- `Right Arrow` / `D`: walk right
- `Space` / `E`: advance text
- `Esc`: toggle pause overlay
- Touch right half: walk right

## Notes

- v0.1 uses generated placeholder art.
- Audio is intentionally stubbed for the first prototype pass.

## ARK DevKit Blueprint Translator

The Blueprint translator lives in `scripts/bp_clipboard_to_prompt.py`.
The `tests/` directory must stay beside `scripts/` at the project root because the tests import the script by path.

Run from copied Blueprint nodes:

```bash
scripts/run_bp_translator.bat
```

Run tests:

```bash
python -m py_compile scripts/bp_clipboard_to_prompt.py
python -m unittest discover -s tests -v
```

Current notes:

- `--provider` is reserved for future integration. The default is `none`, and the script only generates prompts; it does not call Ollama, LM Studio, OpenAI, or Anthropic yet.
- `pseudocode.md` and `cpp_reference.md` are for understanding Blueprint logic. They are not guaranteed to compile or exactly match Unreal generated code.
- Unreal Blueprint `Ctrl+C` text does not include full Class Defaults, Components, inherited defaults, parent class behavior, or native C++ function bodies.
- Use sidecar context files when those details matter: `--defaults-file`, `--components-file`, `--notes-file`, `--parent-class`, `--interfaces`, and `--tags`.
- `--make-context-template` creates a starter context template for Asset name, Parent class, Components, Class Defaults, Replication, Inventory, Stasis, Octree, Radius, Range, Food, Buff, MultiUse, and test observations.
