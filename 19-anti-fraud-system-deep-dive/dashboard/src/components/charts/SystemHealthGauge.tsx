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
import { Box, Typography } from '@mui/material';

interface SystemHealthGaugeProps {
  label: string;
  value: number;
  maxValue?: number;
}

export const SystemHealthGauge: React.FC<SystemHealthGaugeProps> = ({ label, value, maxValue = 100 }) => {
  return (
    <Box sx={{ textAlign: 'center' }}>
      <Typography variant="h6">{label}</Typography>
      <Typography variant="h4" color="primary">
        {value}%
      </Typography>
    </Box>
  );
};
