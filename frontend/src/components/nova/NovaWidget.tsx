import {
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import { Send, X } from "lucide-react";

import {
  novaApi,
  type NovaMessage,
  type NovaPageContextPayload,
} from "@/api/nova";
import { useNovaContext } from "@/contexts/NovaContext";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import { NovaAvatar, NOVA_FRAMES_COUNT } from "./NovaAvatar";

// Floating widget di Nova: avatar animato bottom-right + chat panel
// espandibile. Stato locale (no DB persistence, no localStorage):
//   - `open`: bool — pannello aperto/chiuso
//   - `messages`: lista in-memory dei turni della sessione corrente
//   - `input`: testo nell'input
//   - `welcomeSent`: bool — saluto già richiesto per evitare duplicati
//
// Quando il widget viene chiuso, lo stato resta in memoria finché
// l'utente non ricarica la pagina. Cap soft di 10 turn (20 messaggi)
// inviati nel payload come `history` per mantenere il filo del discorso
// senza esplodere i token.

const HISTORY_PAYLOAD_CAP = 10;

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

function newId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function NovaWidget() {
  const { t, i18n } = useTranslation();
  const novaCtx = useNovaContext();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [welcomeSent, setWelcomeSent] = useState(false);
  const scrollAnchorRef = useRef<HTMLDivElement | null>(null);

  // Frame casuale dell'avatar: cambia ad ogni cambio pagina (e quindi
  // anche tra login diversi, perché il login monta una nuova istanza
  // della layout root e fa partire da un pathname iniziale).
  const [avatarFrame, setAvatarFrame] = useState(
    () => Math.floor(Math.random() * NOVA_FRAMES_COUNT) + 1,
  );
  useEffect(() => {
    setAvatarFrame(Math.floor(Math.random() * NOVA_FRAMES_COUNT) + 1);
  }, [location.pathname]);

  // Lingua corrente UI — passata al BE per generare la risposta nella
  // lingua dell'utente.
  const languageCode = (i18n.language || "it").slice(0, 10);

  // Payload "context" per BE — costruito al volo da `useNovaContext`.
  const contextPayload: NovaPageContextPayload = useMemo(
    () => ({
      page: novaCtx?.page ?? "unknown",
      fields: novaCtx?.fields ?? {},
      org_id: novaCtx?.orgId ?? null,
    }),
    [novaCtx],
  );

  // === Mutations =====================================================

  const welcomeMut = useMutation({
    mutationFn: () =>
      novaApi.welcome({
        context: contextPayload,
        language_code: languageCode,
      }),
    onSuccess: (data) => {
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: "assistant", content: data.message },
      ]);
    },
    onError: () => {
      // Fallback minimal se il BE non risponde
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          content: t("nova.welcomeFallback"),
        },
      ]);
    },
  });

  const chatMut = useMutation({
    mutationFn: (msg: string) => {
      // History: ultimi N messaggi (cap soft per limitare token).
      const history: NovaMessage[] = messages
        .slice(-HISTORY_PAYLOAD_CAP)
        .map((m) => ({ role: m.role, content: m.content }));
      return novaApi.chat({
        message: msg,
        context: contextPayload,
        history,
        language_code: languageCode,
      });
    },
    onSuccess: (data) => {
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: "assistant", content: data.message },
      ]);
    },
    onError: () => {
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          content: t("nova.errorTechnical"),
        },
      ]);
    },
  });

  // === Effects =======================================================

  // Welcome message al primo open del widget (solo una volta per
  // sessione browser).
  useEffect(() => {
    if (!open || welcomeSent) return;
    setWelcomeSent(true);
    welcomeMut.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Auto-scroll al bottom quando arriva un nuovo messaggio.
  useEffect(() => {
    if (!open) return;
    scrollAnchorRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, open, chatMut.isPending]);

  // === Handlers ======================================================

  const handleSend = useCallback(() => {
    const text = input.trim();
    if (!text || chatMut.isPending) return;
    setMessages((prev) => [
      ...prev,
      { id: newId(), role: "user", content: text },
    ]);
    setInput("");
    chatMut.mutate(text);
  }, [input, chatMut]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const isPending = welcomeMut.isPending || chatMut.isPending;

  // === Render ========================================================

  // Bottone collassato: solo l'immagine PNG, senza cerchio/sfondo.
  // Una sola immagine random per sessione/pagina (no animazione).
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={t("nova.openButton")}
        className={cn(
          "fixed bottom-4 right-4 z-40",
          "transition-transform hover:scale-105 active:scale-95",
          "drop-shadow-lg",
        )}
      >
        <NovaAvatar size={112} frame={avatarFrame} />
      </button>
    );
  }

  // Pannello chat aperto.
  return (
    <div
      className={cn(
        "fixed bottom-4 right-4 z-40",
        "flex h-[min(560px,calc(100vh-2rem))] w-[min(380px,calc(100vw-2rem))] flex-col",
        "rounded-xl border border-border bg-card shadow-2xl",
      )}
      role="dialog"
      aria-label={t("nova.title")}
    >
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <NovaAvatar size={40} frame={avatarFrame} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">
            {t("nova.title")}
          </div>
          <div className="truncate text-xs text-muted-foreground">
            {t("nova.subtitle")}
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 shrink-0"
          onClick={() => setOpen(false)}
          aria-label={t("nova.closeButton")}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      {/* Messaggi */}
      <ScrollArea className="flex-1 px-3 py-3">
        <div className="flex flex-col gap-3">
          {messages.map((m) => (
            <ChatBubble key={m.id} role={m.role} content={m.content} />
          ))}
          {isPending && messages.length > 0 && (
            <div className="flex items-center gap-2 self-start text-xs text-muted-foreground">
              <span className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground" />
              <span
                className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground"
                style={{ animationDelay: "150ms" }}
              />
              <span
                className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground"
                style={{ animationDelay: "300ms" }}
              />
            </div>
          )}
          <div ref={scrollAnchorRef} />
        </div>
      </ScrollArea>

      {/* Input */}
      <div className="flex items-end gap-2 border-t border-border px-3 py-2">
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t("nova.placeholder")}
          disabled={isPending}
          rows={1}
          className="min-h-[36px] max-h-32 resize-none text-sm"
        />
        <Button
          type="button"
          size="icon"
          onClick={handleSend}
          disabled={isPending || !input.trim()}
          aria-label={t("nova.send")}
          className="h-9 w-9 shrink-0"
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

// --- Internal ------------------------------------------------------

function ChatBubble({
  role,
  content,
}: {
  role: "user" | "assistant";
  content: string;
}) {
  const isUser = role === "user";
  return (
    <div
      className={cn(
        "flex max-w-[85%] flex-col gap-1 rounded-lg px-3 py-2 text-sm",
        isUser
          ? "self-end bg-primary text-primary-foreground"
          : "self-start bg-muted text-foreground",
      )}
    >
      {/* Render text-only (no dangerouslySetInnerHTML). Preserva newlines. */}
      <span className="whitespace-pre-wrap break-words">{content}</span>
    </div>
  );
}
