# AIFinance Project Context

## Product

AI personal finance application.

Target users:
- US immigrants
- Freelancers
- Small business owners

Core features:
- Upload PDF/Excel statements
- AI categorization
- Monthly report
- AI financial analysis
- Supabase cloud storage

## Tech Stack

Frontend:
- Streamlit

Database:
- Supabase

AI:
- OpenAI

Deployment:
- GitHub
- Streamlit Cloud

## Current Status

Completed:
- User login
- PDF import
- Excel import
- Duplicate detection
- Delete statement
- AI categorization
- AI learning rules
- Monthly report
- AI financial analysis
- Multi-tab UI
- Python transaction normalization
- Initial app.py modularization

## Transaction Data Contract

- Required fields: user_id, date, description, amount, category, source_file, month, unique_key
- Date format: YYYY-MM-DD
- Month format: YYYY-MM and derived from date
- Amount direction: income is positive, expense is negative
- Duplicate detection uses user_id + unique_key in application code
- Transaction normalization is enforced in Python
- No database migration or schema constraint is maintained in this repository

Current module boundaries:
- app.py: Streamlit UI, login, Supabase operations, reporting
- transaction_model.py: transaction validation and normalization
- statement_parsers.py: PDF and Excel parsing

Planned:
- Month-over-month comparison
- Trend charts
- Plaid integration
- Mobile app

## Coding Rules

- Never expose API keys
- Keep Streamlit Cloud compatible
- Keep app.py deployable
- Prefer modular architecture
- Keep transaction normalization in Python at the current project stage
