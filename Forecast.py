# -*- coding: utf-8 -*-
"""Untitled4.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1XarkWLLzVd-M3eL9Zes68T_Go60x3Aic
"""

# Full script: dynamic daily baseline sliding forecasts with PDQ logging
import os, glob, json
from datetime import timedelta
import pandas as pd
import statsmodels.api as sm
import matplotlib.pyplot as plt
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# 0) Set your Drive path manually (no Colab mount)
DRIVE_PATH = '/content/drive/MyDrive'   # ← update this to wherever your Drive is accessible
CSV_FOLDER = os.path.join(DRIVE_PATH, "Daily Average CSV")

# 1) Google Sheets API auth (reads creds.json from Drive)
SHEET_ID = "1XjGGWmET0EdUnXE249osxoIamBROhBci7nSyq4Jz37U"
creds = Credentials.from_service_account_file(
    os.path.join(DRIVE_PATH, "ServiceAccount/creds.json"),
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
sheets = build("sheets", "v4", credentials=creds).spreadsheets()

# 2) Load latest CSV export from Drive
files    = sorted(glob.glob(os.path.join(CSV_FOLDER, "For_Training_*.csv")))
if not files:
    raise FileNotFoundError(f"No CSVs found in {CSV_FOLDER}")
csv_path = files[-1]
print("Loading:", csv_path)

df = pd.read_csv(csv_path, dtype={"Date": str})
df["Date"] = (df["Date"]
    .str.replace(r"\sGMT.*$", "", regex=True)
    .pipe(pd.to_datetime, format="%a %b %d %Y %H:%M:%S")
)
df.rename(columns={
    "Avg H2S":"H2S","Avg CO2":"CO2","Avg CH4":"CH4","Avg MQ4":"MQ4",
    "Avg WindSpeed":"WindSpeed","Avg Humidity":"Humidity","Avg Temperature":"Temperature"
}, inplace=True)
df.set_index("Date", inplace=True)
df = df.asfreq("D").ffill()
print("Data range:", df.index.min().date(), "to", df.index.max().date())

# 3) Forecast window parameters
start_base_end = df.index.min() + timedelta(days=28)   # baseline ends Mar 29
end_base_end   = df.index.max() - timedelta(days=1)    # baseline ends Apr 10
forecast_final = pd.to_datetime("2025-04-17")          # last forecast date

# 4) AIC-based (p,d,q) selection
def select_arima(endog, exog, p_max=5, d_max=1, q_max=5):
    best_aic, best_order = float("inf"), (0,0,0)
    for p in range(p_max+1):
        for d in range(d_max+1):
            for q in range(q_max+1):
                try:
                    mdl = sm.tsa.ARIMA(endog, order=(p,d,q), exog=exog).fit()
                    if mdl.aic < best_aic:
                        best_aic, best_order = mdl.aic, (p,d,q)
                except:
                    continue
    return best_order

# 5) Sliding forecasts + PDQ logging
order_records = []
all_forecasts = []

for base_end in pd.date_range(start=start_base_end, end=end_base_end):
    f_start     = base_end + timedelta(days=1)
    f_end       = min(base_end + timedelta(days=7), forecast_final)
    fdates      = pd.date_range(f_start, f_end)
    exog_future = df[["WindSpeed","Humidity","Temperature"]].reindex(fdates).ffill()

    data = {"Date": fdates}
    for gas in ["H2S","CO2","CH4","MQ4"]:
        # choose baseline start (MQ4 from Mar 8, others from Mar 1)
        if gas == "MQ4":
            window_start = max(df.index.min(), pd.to_datetime("2025-03-08"))
        else:
            window_start = df.index.min()

        endog      = df[gas].loc[window_start:base_end]
        exog_train = df[["WindSpeed","Humidity","Temperature"]].loc[window_start:base_end]

        order = select_arima(endog, exog_train)
        order_records.append({
            "baseline_end": base_end.date(),
            "gas":          gas,
            "p":            order[0],
            "d":            order[1],
            "q":            order[2]
        })
        print(f"{gas}: baseline {window_start.date()}→{base_end.date()}, order={order}")

        mdl   = sm.tsa.ARIMA(endog, order=order, exog=exog_train).fit()
        preds = mdl.get_forecast(steps=len(fdates), exog=exog_future).predicted_mean
        data[f"Forecast_{gas}"] = preds.values

    all_forecasts.append(pd.DataFrame(data))

# 6) Consolidate and filter forecasts
fcast_df = (
    pd.concat(all_forecasts)
      .drop_duplicates("Date", keep="last")
      .reset_index(drop=True)
)
mask = (fcast_df["Date"] >= pd.to_datetime("2025-03-31")) & (fcast_df["Date"] <= forecast_final)
fcast_df = fcast_df.loc[mask].copy()

# 7) Combine with actuals
combined = fcast_df.set_index("Date")
for gas in ["H2S","CO2","CH4","MQ4"]:
    combined[gas] = df[gas].reindex(combined.index)
combined = combined.reset_index()[[
    "Date","H2S","CO2","CH4","MQ4",
    "Forecast_H2S","Forecast_CO2","Forecast_CH4","Forecast_MQ4"
]]

# 8) Append to Google Sheets
body = {"values": [combined.columns.tolist()] + combined.astype(str).values.tolist()}
sheets.values().append(
    spreadsheetId=SHEET_ID,
    range="'Forecasts'!A2",
    valueInputOption="RAW",
    insertDataOption="INSERT_ROWS",
    body=body
).execute()
print("Appended forecasts:", combined["Date"].min().date(), "→", combined["Date"].max().date())

# 9) Print PDQ table
orders_df = pd.DataFrame(order_records)
print("\nPDQ by baseline_end & gas:\n", orders_df)

# 10) Plot CO₂ & MQ₄
plt.figure(figsize=(8,4))
plt.plot(df["CO2"].loc[start_base_end:end_base_end], label="CO₂ Baseline")
plt.plot(fcast_df["Date"], fcast_df["Forecast_CO2"], "o-", label="CO₂ Forecast")
plt.xticks(rotation=45); plt.legend(); plt.tight_layout(); plt.show()

plt.figure(figsize=(8,4))
plt.plot(df["MQ4"].loc[pd.to_datetime("2025-03-08"):end_base_end], label="MQ₄ Baseline")
plt.plot(fcast_df["Date"], fcast_df["Forecast_MQ4"], "o-", label="MQ₄ Forecast")
plt.xticks(rotation=45); plt.legend(); plt.tight_layout(); plt.show()