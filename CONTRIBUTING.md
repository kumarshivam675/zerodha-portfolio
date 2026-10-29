# Contributing

Thanks for taking a look. This is a personal project, but issues and pull requests are welcome.

- **Before a pull request,** run the checks CI runs (see [Development](README.md#development)): ruff,
  mypy and pytest for the API, and eslint and `tsc` for the web app.
- **Changing models.py?** Add an Alembic migration (`alembic revision --autogenerate`) and check that
  `alembic check` reports no drift.
- **Changing an agent's prompt, schema or model?** Run the relevant eval in `apps/api/evals` and put
  the before/after numbers in the pull request. The unit tests can't tell you whether the model got worse.
- **Never commit personal data.** No `.env`, no holdings, no reports containing `{{…}}` figures or your
  average cost. Eval fixtures must contain public market data only.
- Keep pull requests focused, and follow the code around you: type hints, small modules, and comments
  that explain *why*.
