# WeeklySum

Streamlit dashboard for weekly PPH Reporter summaries.

## What It Does

- Upload daily PPH Reporter Excel exports (`.xls` or `.xlsx`).
- Save parsed report data in a local SQLite database inside the Streamlit app.
- Show weekly PPH trend and total volume trend.
- Show daily summary, group weekly summary, and DNE notes.
- Export a weekly summary workbook.

## Local Run

```powershell
cd WeeklySum
python -m pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Community Cloud Deploy

1. Create a GitHub repository and upload the `WeeklySum` folder.
2. Go to `https://share.streamlit.io`.
3. Click **Create app**.
4. Select your repository, branch, and set app file path to:

```text
WeeklySum/app.py
```

5. Deploy.
6. Share the generated `streamlit.app` link with your boss.

## Optional Password

In Streamlit Community Cloud, open **Advanced settings** and add:

```toml
APP_PASSWORD = "your-password"
```

If no password is set, the app is open to anyone with the link.

## Storage Note

This app stores data in `data/weeklysum.sqlite` inside the Streamlit app. Streamlit Community Cloud local storage is convenient but not guaranteed permanent after reboot or redeploy. Use the **Download database backup** button regularly, and restore it if needed.
