"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isBackendOnline, postChat } from "../lib/api";
import { clearThread, loadThread, saveThread } from "../lib/thread";
import type { Message } from "../lib/types";
import { MAX_MESSAGE_LENGTH } from "../lib/types";

interface UseChatReturn {
  messages: Message[];
  loading: boolean;
  /** Last send failure (message text), if any. */
  error: string | null;
  /** Backend liveness: null while unknown. */
  online: boolean | null;
  sendMessage: (text: string) => Promise<void>;
  /** Abort an in-flight request (does not remove messages). */
  stop: () => void;
  clearMessages: () => void;
  /** Re-send the last user message (after a failure). */
  retryLast: () => void;
  /** Hide the error banner without touching the thread. */
  clearError: () => void;
}

export function useChat(): UseChatReturn {
  const [messages, setMessages] = useState<Message[]>(loadThread);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Persist the thread on every change — it survives refresh/restart.
  useEffect(() => {
    saveThread(messages);
  }, [messages]);

  // Connection indicator: check on mount, then on a slow interval and
  // after every failed send.
  const ping = useCallback(() => {
    isBackendOnline().then(setOnline);
  }, []);
  useEffect(() => {
    ping();
    const id = window.setInterval(ping, 30_000);
    return () => window.clearInterval(id);
  }, [ping]);

  // Abort any in-flight request on unmount.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
  }, []);

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || loading) return;
      if (trimmed.length > MAX_MESSAGE_LENGTH) {
        setError(`Message is too long (max ${MAX_MESSAGE_LENGTH.toLocaleString()} characters).`);
        return;
      }

      const prior: Message[] = messages; // thread before this message
      const userMsg: Message = {
        role: "user",
        content: trimmed,
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setLoading(true);
      setError(null);

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const answer = await postChat(
          trimmed,
          prior.map(({ role, content }) => ({ role, content })),
          controller.signal
        );
        const agentMsg: Message = {
          role: "agent",
          content: answer,
          timestamp: Date.now(),
        };
        setMessages((prev) => [...prev, agentMsg]);
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        const msg =
          err instanceof Error ? err.message : "Unknown error occurred";
        setError(msg);
        setOnline(false);
        setMessages((prev) => [
          ...prev,
          { role: "agent", content: msg, timestamp: Date.now(), error: true },
        ]);
      } finally {
        setLoading(false);
        abortRef.current = null;
      }
    },
    [loading, messages]
  );

  const clearMessages = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setError(null);
    clearThread();
  }, []);

  // Keep a fresh reference for retry without creating a dependency cycle.
  const clearError = useCallback(() => {
    setError(null);
  }, []);

  const sendMessageRef = useRef(sendMessage);
  useEffect(() => {
    sendMessageRef.current = sendMessage;
  }, [sendMessage]);

  const retryLast = useCallback(() => {
    // Find the last real user message, drop everything after its failure,
    // then re-send it through the normal path (with the corrected thread).
    const idx = [...messages].reverse().findIndex((m) => m.role === "user");
    if (idx === -1) return;
    const lastUser = messages[messages.length - 1 - idx];
    setMessages((prev) => prev.slice(0, -(idx + 1))); // drop last user + failure
    window.setTimeout(() => void sendMessageRef.current(lastUser.content), 0);
  }, [messages]);

  return {
    messages,
    loading,
    error,
    online,
    sendMessage,
    stop,
    clearMessages,
    retryLast,
    clearError,
  };
}
