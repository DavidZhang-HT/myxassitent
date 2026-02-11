# MyXAssistant

MyXAssistant (formerly MyXAssistant) is a tool to analyze and visualize your Twitter/X likes.

## Features
- **Import Data**: Import your Twitter likes from a JSON file.
- **Search & Filter**: Search through your liked tweets, filter by category or author.
- **Analysis**: View statistics about your likes, including top authors and category breakdown.
- **Local Database**: All data is stored locally in an SQLite database.

## Setup

1.  Clone the repository:
    ```bash
    git clone https://github.com/DavidZhang-HT/myxassitent
    cd myxassitent
    ```

2.  Install dependencies:
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

3.  Import your data:
    ```bash
    # Assuming you have a likes.json file
    python import_data.py path/to/likes.json
    ```

4.  Run the application:
    ```bash
    python app.py
    ```
    Open http://127.0.0.1:5000 in your browser.

## License
MIT
