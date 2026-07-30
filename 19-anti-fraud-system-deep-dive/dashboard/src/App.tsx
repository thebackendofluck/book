// Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import React from 'react';
import { Routes, Route } from 'react-router-dom';
import { Box, Container, AppBar, Toolbar, Typography } from '@mui/material';
import { Dashboard } from './components/Dashboard';
import { AlertsPage } from './components/AlertsPage';
import { ModelsPage } from './components/ModelsPage';
import { SettingsPage } from './components/SettingsPage';
import { Navigation } from './components/Navigation';

function App() {
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      <AppBar position="static" elevation={1}>
        <Toolbar>
          <Typography variant="h6" component="div" sx={{ flexGrow: 1 }}>
            Fraud Detection System
          </Typography>
        </Toolbar>
      </AppBar>

      <Box sx={{ display: 'flex', flexGrow: 1 }}>
        <Navigation />

        <Box component="main" sx={{ flexGrow: 1, p: 3 }}>
          <Container maxWidth="xl">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/alerts" element={<AlertsPage />} />
              <Route path="/models" element={<ModelsPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </Container>
        </Box>
      </Box>
    </Box>
  );
}

export default App;