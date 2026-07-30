// Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import { useEffect, useRef, useState } from 'react';

interface WebSocketMessage {
  type: string;
  data: any;
  timestamp: string;
}

export const useWebSocket = (url: string) => {
  const [lastMessage, setLastMessage] = useState<WebSocketMessage | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<'connecting' | 'connected' | 'disconnected' | 'error'>('disconnected');
  const ws = useRef<WebSocket | null>(null);
  const reconnectTimeout = useRef<NodeJS.Timeout>();
  const reconnectAttempts = useRef(0);
  const maxReconnectAttempts = 5;
  const reconnectInterval = 3000; // 3 seconds

  const connect = () => {
    try {
      setConnectionStatus('connecting');
      ws.current = new WebSocket(url);

      ws.current.onopen = () => {
        setIsConnected(true);
        setConnectionStatus('connected');
        reconnectAttempts.current = 0;
        console.log('WebSocket connected');
      };

      ws.current.onmessage = (event) => {
        try {
          const message: WebSocketMessage = JSON.parse(event.data);
          setLastMessage(message);
        } catch (error) {
          console.error('Failed to parse WebSocket message:', error);
        }
      };

      ws.current.onclose = (event) => {
        setIsConnected(false);
        setConnectionStatus('disconnected');
        console.log('WebSocket disconnected:', event.code, event.reason);

        // Attempt to reconnect if not a normal closure
        if (event.code !== 1000 && reconnectAttempts.current < maxReconnectAttempts) {
          reconnectAttempts.current += 1;
          reconnectTimeout.current = setTimeout(() => {
            console.log(`Attempting to reconnect (${reconnectAttempts.current}/${maxReconnectAttempts})`);
            connect();
          }, reconnectInterval);
        }
      };

      ws.current.onerror = (error) => {
        setConnectionStatus('error');
        console.error('WebSocket error:', error);
      };

    } catch (error) {
      setConnectionStatus('error');
      console.error('Failed to create WebSocket connection:', error);
    }
  };

  useEffect(() => {
    connect();

    return () => {
      if (reconnectTimeout.current) {
        clearTimeout(reconnectTimeout.current);
      }
      if (ws.current) {
        ws.current.close(1000, 'Component unmounting');
      }
    };
  }, [url]);

  const sendMessage = (message: any) => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(message));
      return true;
    }
    return false;
  };

  const disconnect = () => {
    if (reconnectTimeout.current) {
      clearTimeout(reconnectTimeout.current);
    }
    if (ws.current) {
      ws.current.close(1000, 'Manual disconnect');
    }
  };

  return {
    lastMessage,
    isConnected,
    connectionStatus,
    sendMessage,
    disconnect
  };
};