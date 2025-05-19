# Data process

## Overview
This module provides tools for crawling, predicting, and visualizing cryptocurrency prices using data science techniques. It leverages the NAMI API to collect historical price data and applies time series forecasting models to generate predictions. The project is designed to streamline the process of data acquisition, analysis, and visualization for cryptocurrency market trends.

## Project Structure
```
AI_Agent_SEA25
├── data
│   ├── data_crawler.py        # Fetches cryptocurrency prices and saves them to a database.
│   ├── predict.py             # Contains functions for making predictions using the ARIMA model.
│   └── config
│       └── db.py             # Database configuration settings.
└── README.md                   # Documentation for the project.
```

## Setup Instructions
1. **Clone the repository:**
   ```
   git clone https://github.com/Vistia-AI/AI_Agent_SEA25.git
   cd AI_Agent_SEA25
   ```

2. **Create a virtual environment:**
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

3. **Install the required packages:**
   ```
   pip install -r requirements.txt
   ```

4. **Set up environment variables:**
   - Copy the `.env.example` file to `.env` and fill in the necessary configuration details.

## Usage
- To fetch cryptocurrency prices and save them to the database, run:
  ```
  python data/data_crawler.py
  ```

- To make predictions using the ARIMA model, run:
  ```
  python data/predict.py
  ```

## Contributing
Contributions are welcome! Please open an issue or submit a pull request for any improvements or bug fixes.

## License
This project is licensed under the MIT License. See the LICENSE file for more details.