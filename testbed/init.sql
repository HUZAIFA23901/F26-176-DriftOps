-- ==============================================================================
-- DriftOps Warehouse Initialization Script
-- Project ID: F26-176-DriftOps
-- Target: testbed/init.sql
-- ==============================================================================

-- Create target schemas
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS analytics;

-- Drop table if exists to ensure idempotent execution
DROP TABLE IF EXISTS raw.raw_customers CASCADE;

-- Source Operational Table
CREATE TABLE raw.raw_customers (
    id INT PRIMARY KEY,
    customer_phone VARCHAR(50),
    email VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seed realistic sample customers
INSERT INTO raw.raw_customers (id, customer_phone, email, created_at) VALUES
    (1, '+1-555-0101', 'elena.rostova@example.com', '2024-01-15 08:30:00'),
    (2, '+1-555-0102', 'marcus.vance@example.com', '2024-01-16 09:45:12'),
    (3, '+1-555-0103', 'sarah.connor@example.com', '2024-01-17 11:20:05'),
    (4, '+1-555-0104', 'kenji.sato@example.com', '2024-01-18 14:15:30'),
    (5, '+1-555-0105', 'amara.okafor@example.com', '2024-01-19 16:50:22'),
    (6, '+1-555-0106', 'lucas.silva@example.com', '2024-01-20 10:05:40'),
    (7, '+1-555-0107', 'chloe.dupont@example.com', '2024-01-21 13:40:15'),
    (8, '+1-555-0108', 'liam.gallagher@example.com', '2024-01-22 15:25:50'),
    (9, '+1-555-0109', 'priya.sharma@example.com', '2024-01-23 17:10:08'),
    (10, '+1-555-0110', 'david.kim@example.com', '2024-01-24 19:35:45');
