# Project rules

- Never commit credentials, tokens, or local secrets.
- Never log email contents or tokens.
- Never invent missing opportunity data. Use Python `None` for missing values and JSON `null` in serialized output.
- Keep modules independent and use explicit interfaces between processing stages.
- Write pytest tests for every module as its behavior is implemented.
- Claim a feature works only after running its tests successfully.
