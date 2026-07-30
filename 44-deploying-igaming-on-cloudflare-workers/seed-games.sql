-- AcmeToCasino — Game Catalog Seed Data
-- 20 games across 5 categories with realistic RTP values

INSERT INTO games (game_id, provider, name, category, type, rtp, mobile_compatible, is_active) VALUES
('aviator', 'AcmeToCasino', 'Aviator', 'crash', 'instant', 97.0, 1, 1),
('fortune-tiger', 'AcmeToCasino', 'Fortune Tiger', 'slots', 'slots', 96.8, 1, 1),
('gates-of-olympus', 'AcmeToCasino', 'Gates of Olympus', 'slots', 'slots', 96.5, 1, 1),
('sweet-bonanza', 'AcmeToCasino', 'Sweet Bonanza', 'slots', 'slots', 96.5, 1, 1),
('blackjack', 'AcmeToCasino', 'Blackjack', 'table', 'table', 99.5, 1, 1),
('roulette', 'AcmeToCasino', 'European Roulette', 'table', 'table', 97.3, 1, 1),
('lightning-roulette', 'Evolution', 'Lightning Roulette', 'live', 'live', 97.3, 1, 1),
('plinko', 'AcmeToCasino', 'Plinko', 'crash', 'instant', 97.0, 1, 1),
('mines', 'AcmeToCasino', 'Mines', 'crash', 'instant', 97.0, 1, 1),
('dice', 'AcmeToCasino', 'Dice', 'crash', 'instant', 99.0, 1, 1),
('baccarat', 'AcmeToCasino', 'Baccarat', 'table', 'table', 98.9, 1, 1),
('texas-holdem', 'AcmeToCasino', 'Texas Hold''em', 'table', 'table', 97.8, 1, 1),
('crazy-time', 'Evolution', 'Crazy Time', 'live', 'live', 95.5, 1, 1),
('starburst', 'NetEnt', 'Starburst', 'slots', 'slots', 96.1, 1, 1),
('wolf-gold', 'Pragmatic Play', 'Wolf Gold', 'slots', 'slots', 96.0, 1, 1),
('book-of-dead', 'Play''n GO', 'Book of Dead', 'slots', 'slots', 96.2, 1, 1),
('mega-moolah', 'Microgaming', 'Mega Moolah', 'slots', 'slots', 88.1, 1, 1),
('tower', 'AcmeToCasino', 'Tower', 'crash', 'instant', 97.0, 1, 1),
('double', 'AcmeToCasino', 'Double', 'crash', 'instant', 98.0, 1, 1),
('hi-lo', 'AcmeToCasino', 'Hi-Lo', 'crash', 'instant', 97.0, 1, 1);
