# Sample Quotes

This folder is intended to hold 5–10 anonymized demo renovation quotes
to be used for testing and demonstration purposes.

## Guidelines

- All quotes should be fully anonymized — no real names, addresses, phone
  numbers, or other personally identifiable information.
- Use generic Hungarian addresses (e.g. "1011 Minta utca 1.") with no
  door numbers.
- Remove or genericize any contractor names, client names, and contact
  details.
- Keep the cost data realistic so the pipeline has meaningful data to
  train on and query against.

## How to add quotes

1. Take a real quote from `data/raw/quotes/<year>/` (in the source repo)
2. Anonymize it manually (address, names, contact info)
3. Place the anonymized XLSX file in this directory
4. Run `python scripts/run_ingestion.py --quotes-dir data/sample_quotes/`
   to process them into JSON and Markdown

No automated anonymization is provided — human judgment is required to
determine what is safe to include.
