// Companion code for "The Backend of Luck" - Chapter 14, Mobile-First Architecture for iGaming.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Chapter 7: Mobile-First Architecture for iGaming
 * React Native Redux Store Configuration
 *
 * This module implements the core Redux Toolkit state management for a
 * React Native gambling application, including:
 * - GameState interface and gameSlice reducer with balance/bet management
 * - casinoApi RTK Query slice with optimistic bet placement and retry logic
 * - Persisted store configuration with encrypted AsyncStorage serialization
 * - Regulatory audit trail for balance updates
 *
 * Reference: Chapter 7 - React Native Architecture section
 */

import { configureStore, createSlice, PayloadAction } from '@reduxjs/toolkit';
import { persistStore, persistReducer } from 'redux-persist';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { createApi, fetchBaseQuery } from '@reduxjs/toolkit/query/react';

// Game State Management
interface GameState {
  currentGame: string | null;
  balance: number;
  bets: Array<{
    id: string;
    amount: number;
    gameId: string;
    timestamp: number;
    status: 'pending' | 'settled' | 'cancelled';
  }>;
  sessionId: string;
  jurisdiction: string;
}

const gameSlice = createSlice({
  name: 'game',
  initialState: {
    currentGame: null,
    balance: 0,
    bets: [],
    sessionId: '',
    jurisdiction: 'UK'
  } as GameState,
  reducers: {
    setCurrentGame: (state, action: PayloadAction<string>) => {
      state.currentGame = action.payload;
    },
    updateBalance: (state, action: PayloadAction<number>) => {
      state.balance = action.payload;
      // Audit trail for regulatory compliance
      console.log(`[AUDIT] Balance updated: ${action.payload} at ${new Date().toISOString()}`);
    },
    placeBet: (state, action: PayloadAction<{id: string, amount: number, gameId: string}>) => {
      state.bets.push({
        ...action.payload,
        timestamp: Date.now(),
        status: 'pending'
      });
      state.balance -= action.payload.amount;
    }
  }
});

// API Slice with automatic retry and offline support
export const casinoApi = createApi({
  reducerPath: 'casinoApi',
  baseQuery: fetchBaseQuery({
    baseUrl: 'https://api.casino.com/v1/',
    prepareHeaders: (headers, { getState }) => {
      const token = (getState() as any).auth.token;
      if (token) {
        headers.set('authorization', `Bearer ${token}`);
      }
      headers.set('X-Device-Id', DeviceInfo.getUniqueId());
      headers.set('X-App-Version', DeviceInfo.getVersion());
      return headers;
    }
  }),
  tagTypes: ['Game', 'Balance', 'Bets'],
  endpoints: (builder) => ({
    getGameState: builder.query<GameState, string>({
      query: (gameId) => `games/${gameId}/state`,
      providesTags: ['Game'],
      // Automatic retry with exponential backoff
      extraOptions: {
        maxRetries: 3,
        backoff: (attempt: number) => Math.min(1000 * 2 ** attempt, 30000)
      }
    }),
    placeBet: builder.mutation({
      query: ({ gameId, amount, betData }) => ({
        url: `games/${gameId}/bets`,
        method: 'POST',
        body: { amount, betData }
      }),
      invalidatesTags: ['Balance', 'Bets'],
      // Optimistic updates for better UX
      async onQueryStarted({ amount }, { dispatch, queryFulfilled }) {
        dispatch(gameSlice.actions.placeBet({
          id: generateBetId(),
          amount,
          gameId: 'current'
        }));

        try {
          await queryFulfilled;
        } catch (error) {
          // Rollback on failure
          dispatch(gameSlice.actions.updateBalance(
            (getState() as any).game.balance + amount
          ));
        }
      }
    })
  })
});

// Store configuration with persistence
const persistConfig = {
  key: 'root',
  storage: AsyncStorage,
  whitelist: ['auth', 'game', 'preferences'], // Only persist critical data
  // Custom serialization for regulatory compliance
  transforms: [
    {
      in: (state: any) => {
        // Encrypt sensitive data
        return encryptSensitiveData(state);
      },
      out: (state: any) => {
        // Decrypt and validate
        return decryptAndValidate(state);
      }
    }
  ]
};

export const store = configureStore({
  reducer: {
    game: gameSlice.reducer,
    [casinoApi.reducerPath]: casinoApi.reducer,
    // Other reducers...
  },
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware({
      serializableCheck: {
        ignoredActions: ['persist/PERSIST', 'persist/REHYDRATE']
      }
    }).concat(casinoApi.middleware),
});

export const persistor = persistStore(store);
