import { useEffect, useRef, useCallback, useState } from "react";
import { createWebSocket } from "../api/arbApi";
import type { WebSocketMessage } from "../types/arbitrage";

interface UseWebSocketReturn {
  isConnected: boolean;
}

export function useWebSocket(
  onMessage: (msg: WebSocketMessage) => void
): UseWebSocketReturn {
  const wsRef       = useRef<WebSocket | null>(null);
  const retryDelay  = useRef(1000);
  const [isConnected, setIsConnected] = useState(false);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const connect = useCallback(() => {
    const ws = createWebSocket();
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      retryDelay.current = 1000;
    };

    ws.onmessage = (event) => {
      try {
        const msg: WebSocketMessage = JSON.parse(event.data);
        onMessageRef.current(msg);
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      const delay = Math.min(retryDelay.current, 30_000);
      retryDelay.current = delay * 2;
      setTimeout(connect, delay);
    };

    ws.onerror = () => ws.close();
  }, []);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  return { isConnected };
}
