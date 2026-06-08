# Translation files

Run `pybabel extract -F babel.cfg -k _l -o messages.pot .` from project root,
then `pybabel init -i messages.pot -d app/translations -l <code>` for each language.

See README.md → "i18n workflow" section for full process.
