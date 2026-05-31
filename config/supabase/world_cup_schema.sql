-- 2026 FIFA World Cup Prediction System Schema
-- Supabase / PostgreSQL Database Architecture

-- Enable UUID extension if not already enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Teams & Ratings (Ignoring official FIFA rankings, using Elo)
CREATE TABLE IF NOT EXISTS teams (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    country_name VARCHAR(100) NOT NULL UNIQUE,
    continent VARCHAR(50) NOT NULL,
    elo_rating INTEGER NOT NULL DEFAULT 1500,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Mapping for API integration (e.g., API-Football to internal UUIDs)
CREATE TABLE IF NOT EXISTS team_mapping (
    api_provider VARCHAR(50) NOT NULL,
    api_team_id VARCHAR(100) NOT NULL,
    internal_team_id UUID REFERENCES teams(id) ON DELETE CASCADE,
    PRIMARY KEY (api_provider, api_team_id)
);

-- 2. Venues & Environmental Factors
CREATE TABLE IF NOT EXISTS venues (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(100) NOT NULL,
    city VARCHAR(100) NOT NULL,
    country VARCHAR(100) NOT NULL, -- USA, Canada, Mexico
    altitude_meters INTEGER NOT NULL DEFAULT 0,
    climate_type VARCHAR(50), -- e.g., 'Humid Subtropical', 'Desert'
    timezone VARCHAR(50)
);

-- 3. Match Data
CREATE TABLE IF NOT EXISTS matches (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    api_match_id VARCHAR(100) UNIQUE NOT NULL,
    home_team_id UUID REFERENCES teams(id),
    away_team_id UUID REFERENCES teams(id),
    venue_id UUID REFERENCES venues(id),
    kickoff_time TIMESTAMP WITH TIME ZONE NOT NULL,
    stage VARCHAR(50) NOT NULL, -- e.g., 'Group', 'R16', 'Quarter'
    home_score INTEGER,
    away_score INTEGER,
    status VARCHAR(20) DEFAULT 'SCHEDULED'
);

-- 4. Starting XI & Squad Quality (Populated by n8n 45 mins before kickoff)
CREATE TABLE IF NOT EXISTS match_lineups (
    match_id UUID REFERENCES matches(id) ON DELETE CASCADE,
    team_id UUID REFERENCES teams(id) ON DELETE CASCADE,
    avg_ea_fc_rating NUMERIC(4,2), -- e.g., 84.50
    total_market_value BIGINT, -- in Euros
    star_player_count INTEGER, -- Number of players with >= 85 rating
    is_confirmed BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (match_id, team_id)
);

-- 5. Prediction Results & Power Scores
CREATE TABLE IF NOT EXISTS match_predictions (
    match_id UUID PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
    home_power_score NUMERIC(5,2),
    away_power_score NUMERIC(5,2),
    predicted_result VARCHAR(5),
    confidence_score NUMERIC(5,2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- RLS (Row Level Security) Policies
ALTER TABLE teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_mapping ENABLE ROW LEVEL SECURITY;
ALTER TABLE venues ENABLE ROW LEVEL SECURITY;
ALTER TABLE matches ENABLE ROW LEVEL SECURITY;
ALTER TABLE match_lineups ENABLE ROW LEVEL SECURITY;
ALTER TABLE match_predictions ENABLE ROW LEVEL SECURITY;

-- Allow public read access (Modify these if using authenticated clients)
CREATE POLICY "Public Read Access" ON teams FOR SELECT USING (true);
CREATE POLICY "Public Read Access" ON venues FOR SELECT USING (true);
CREATE POLICY "Public Read Access" ON matches FOR SELECT USING (true);
CREATE POLICY "Public Read Access" ON match_predictions FOR SELECT USING (true);

-- Allow authenticated service roles (like n8n) to insert/update
-- (Requires Supabase service_role key to bypass RLS or specific policies)
