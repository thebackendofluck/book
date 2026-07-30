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
import { List, ListItem, ListItemButton, ListItemIcon, ListItemText, Drawer } from '@mui/material';
import { useNavigate, useLocation } from 'react-router-dom';
import DashboardIcon from '@mui/icons-material/Dashboard';
import NotificationsIcon from '@mui/icons-material/Notifications';
import ModelTrainingIcon from '@mui/icons-material/ModelTraining';
import SettingsIcon from '@mui/icons-material/Settings';

const drawerWidth = 240;

const navItems = [
  { text: 'Dashboard', path: '/', icon: <DashboardIcon /> },
  { text: 'Alerts', path: '/alerts', icon: <NotificationsIcon /> },
  { text: 'Models', path: '/models', icon: <ModelTrainingIcon /> },
  { text: 'Settings', path: '/settings', icon: <SettingsIcon /> },
];

export const Navigation: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <Drawer
      variant="permanent"
      sx={{
        width: drawerWidth,
        flexShrink: 0,
        '& .MuiDrawer-paper': { width: drawerWidth, boxSizing: 'border-box', top: 64 },
      }}
    >
      <List>
        {navItems.map((item) => (
          <ListItem key={item.text} disablePadding>
            <ListItemButton
              selected={location.pathname === item.path}
              onClick={() => navigate(item.path)}
            >
              <ListItemIcon>{item.icon}</ListItemIcon>
              <ListItemText primary={item.text} />
            </ListItemButton>
          </ListItem>
        ))}
      </List>
    </Drawer>
  );
};
