"""
Hybrid XGBoost Pipeline (Top 5 Europe + Summer Leagues).
Replaces the static 'Summer Modifier' logic by embedding summer features directly 
into the machine learning model. This allows XGBoost to learn non-linear relationships 
(e.g., travel distance impact varies by league).
"""
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Dummy libraries to simulate scikit-learn and xgboost
try:
    from sklearn.preprocessing import OneHotEncoder
    from sklearn.compose import ColumnTransformer
    import xgboost as xgb
except ImportError:
    pass

def extract_hybrid_features(db_cursor, seasons=['2023', '2024', '2025']):
    """
    Extracts match data from SQLite, including new Summer League features.
    """
    season_placeholder = ','.join('?' * len(seasons))
    query = f"""
        SELECT 
            league_code,
            home_team_id,
            away_team_id,
            pitch_type,
            travel_distance_km,
            cup_rotation_fatigue,
            dp_presence,
            weather_condition,
            is_summer_league,
            congestion_advantage,
            ft_result -- Target variable (0: Away, 1: Draw, 2: Home)
        FROM matches
        WHERE season IN ({season_placeholder})
    """
    db_cursor.execute(query, seasons)
    columns = [desc[0] for desc in db_cursor.description]
    data = db_cursor.fetchall()
    
    df = pd.DataFrame(data, columns=columns)
    
    # Preprocessing numerical features
    df['travel_distance_km'] = df['travel_distance_km'].fillna(0)
    df['dp_presence'] = df['dp_presence'].fillna(0)
    df['cup_rotation_fatigue'] = df['cup_rotation_fatigue'].fillna(0).astype(int)
    df['congestion_advantage'] = df['congestion_advantage'].fillna(0.0)
    
    # Preprocessing categorical features
    df['pitch_type'] = df['pitch_type'].fillna('NATURAL')
    df['weather_condition'] = df['weather_condition'].fillna('NORMAL')
    df['league_code'] = df['league_code'].fillna('UNKNOWN')
    
    return df

def build_and_train_hybrid_xgb(df: pd.DataFrame):
    """
    Builds the XGBoost model utilizing categorical encodings for league and pitch type.
    """
    logger.info("Initializing Hybrid XGBoost Pipeline...")
    
    # Drop rows with invalid targets
    df = df[df['ft_result'].isin(['H', 'D', 'A'])].copy()
    
    # Separate Features (X) and Target (y)
    X = df.drop(columns=['ft_result', 'home_team_id', 'away_team_id'])
    
    # Map 'H', 'D', 'A' to 0, 1, 2
    label_map = {'A': 0, 'D': 1, 'H': 2}
    y = df['ft_result'].map(label_map)
    
    # Define categorical columns to One-Hot Encode
    categorical_cols = ['league_code', 'pitch_type', 'weather_condition']
    numerical_cols = ['travel_distance_km', 'cup_rotation_fatigue', 'dp_presence', 'is_summer_league', 'congestion_advantage']
    
    try:
        # Create ColumnTransformer for preprocessing
        preprocessor = ColumnTransformer(
            transformers=[
                ('cat', OneHotEncoder(handle_unknown='ignore', sparse_output=False), categorical_cols),
                ('num', 'passthrough', numerical_cols)
            ])
            
        # Transform data
        X_processed = preprocessor.fit_transform(X)
        
        # XGBoost Classifier
        model = xgb.XGBClassifier(
            n_estimators=500,
            learning_rate=0.01,
            max_depth=6,
            subsample=0.8,
            colsample_bytree=0.8,
            objective='multi:softprob',
            num_class=3
        )
        
        # Train model
        model.fit(X_processed, y)
        logger.info("Hybrid XGBoost Model trained successfully.")
        
        # Extract Feature Importances
        try:
            cat_features = preprocessor.named_transformers_['cat'].get_feature_names_out(categorical_cols)
            all_features = list(cat_features) + numerical_cols
            importances = model.feature_importances_
            
            # Print top 5 features
            print("\n--- XGBoost Feature Importances ---")
            feat_imp = sorted(zip(all_features, importances), key=lambda x: x[1], reverse=True)
            for f, imp in feat_imp[:10]:
                print(f"{f}: {imp:.4f}")
            print("-----------------------------------")
        except Exception as e:
            pass
            
        return model, preprocessor
        
    except Exception as e:
        logger.error(f"Error training Hybrid XGBoost: {e}")
        return None, None
