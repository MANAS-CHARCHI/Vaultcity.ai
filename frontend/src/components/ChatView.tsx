import React, { useState, useRef, useEffect } from "react";
import { Sparkles, Copy, Check, FileText } from "lucide-react";
import { ChatMessage } from "../types";

interface ChatViewProps {
  messages: ChatMessage[];
  onSendMessage: (query: string) => void;
  isLoading: boolean;
  onOpenDocument?: (filename: string) => void;
}

/**
 * Interactive Copyable Entity Pill.
 * Enables 1-click copying of specific names, numbers, IDs, dates, or values.
 */
const CopyablePill: React.FC<{ value: string }> = ({ value }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      title="Click to copy this value only"
      className="inline-flex items-center gap-1 px-1.5 py-0.5 mx-0.5 my-0.5 rounded font-mono text-[11px] sm:text-xs font-semibold bg-orange-500/10 hover:bg-orange-500/20 text-stone-900 dark:text-orange-200 border border-orange-500/30 hover:border-orange-500/60 transition-all cursor-pointer shadow-2xs active:scale-95 group select-all"
    >
      <span>{value}</span>
      {copied ? (
        <span className="flex items-center gap-0.5 text-emerald-600 dark:text-emerald-400 font-sans text-[10px] font-bold">
          <Check className="w-3 h-3 stroke-[2.5]" />
          <span>Copied!</span>
        </span>
      ) : (
        <Copy className="w-3 h-3 text-stone-400 group-hover:text-orange-600 dark:group-hover:text-orange-300 stroke-[2] transition-colors" />
      )}
    </button>
  );
};

export const ChatView: React.FC<ChatViewProps> = ({
  messages,
  onSendMessage,
  isLoading,
  onOpenDocument,
}) => {
  const [input, setInput] = useState("");
  const [copiedFullId, setCopiedFullId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const quickPrompts = [
    { label: "PAN number", query: "what is my PAN card number?" },
    { label: "Aadhaar number", query: "give me my aadhaar number" },
    { label: "Who am I?", query: "who am i" },
    { label: "Salary / Pay", query: "how much is my salary or pay" },
    { label: "Father's name", query: "what is my father's name?" },
  ];

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;
    onSendMessage(input.trim());
    setInput("");
  };

  const handleCopyFull = (msgId: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedFullId(msgId);
    setTimeout(() => setCopiedFullId(null), 1800);
  };

  /**
   * Parse formatted text to render:
   * 1. Inline `value` backticks as interactive 1-click CopyablePills
   * 2. In-text @va/filename references as clickable document preview triggers
   */
  const renderFormattedText = (text: string) => {
    const parts = text.split(/(`[^`]+`|@va\/[^\s,;:()]+)/g);
    return parts.map((part, idx) => {
      if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
        const cleanVal = part.slice(1, -1);
        return <CopyablePill key={idx} value={cleanVal} />;
      }
      if (part.startsWith("@va/")) {
        const docName = part.slice(4);
        return (
          <button
            key={idx}
            type="button"
            onClick={() => onOpenDocument?.(docName)}
            title={`Click to view ${docName} in vault`}
            className="inline-flex items-center gap-0.5 px-1.5 py-0.2 mx-0.5 rounded text-[10px] font-mono font-medium bg-orange-500/10 hover:bg-orange-500/20 text-orange-700 dark:text-orange-400 border border-orange-500/25 transition-all cursor-pointer underline decoration-orange-400/50 hover:decoration-orange-500"
          >
            <FileText className="w-2.5 h-2.5 opacity-80" />
            <span>{part}</span>
          </button>
        );
      }
      return <span key={idx}>{part}</span>;
    });
  };

  return (
    <div className="w-full max-w-2xl mx-auto flex flex-col h-full md:h-[calc(100vh-160px)] md:max-h-[620px] md:min-h-[460px] bg-[#FFFCF9] md:bg-white dark:bg-[#140E0A] md:dark:bg-[#1C1510] border-0 md:border border-orange-200/80 dark:border-[#2D2118] rounded-none md:rounded-2xl shadow-none md:shadow-val overflow-hidden transition-all relative">
      {/* Clean Desktop Top Bar */}
      <div className="hidden md:flex shrink-0 px-4 py-2.5 border-b border-orange-200/60 dark:border-[#2D2118] items-center justify-between bg-orange-50/60 dark:bg-[#221913]">
        <div className="flex items-center space-x-2">
          <div className="w-4 h-4 rounded bg-orange-600 flex items-center justify-center text-white text-[9px] font-bold">
            vc
          </div>
          <span className="text-xs font-bold text-stone-900 dark:text-white font-sans">
            vault assistant
          </span>
        </div>
      </div>

      {/* Message Stream */}
      <div className="flex-1 overflow-y-auto px-3.5 py-4 space-y-3.5 pb-4">
        {/* Welcome Screen when no messages */}
        {messages.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center text-center px-4 py-8 pb-24 md:pb-8">
            <div className="w-11 h-11 rounded-2xl bg-orange-500/10 border border-orange-500/20 flex items-center justify-center text-orange-600 dark:text-orange-400 mb-3 shadow-xs">
              <Sparkles className="w-5 h-5 stroke-[1.75]" />
            </div>
            <h3 className="text-sm font-bold text-stone-900 dark:text-white mb-1 font-sans">
              Ask Your Personal Vault
            </h3>
            <p className="text-xs text-stone-500 dark:text-stone-400 max-w-sm mb-5 leading-relaxed">
              Ask any question about your documents. Answers show the exact required information with 1-click copy:
            </p>

            <div className="flex flex-wrap justify-center gap-2 max-w-md">
              {quickPrompts.map((p, idx) => (
                <button
                  key={idx}
                  onClick={() => onSendMessage(p.query)}
                  className="px-3 py-1.5 rounded-lg bg-white dark:bg-[#1C1510] hover:bg-orange-50 dark:hover:bg-[#251A12] border border-orange-200/80 dark:border-[#2D2118] text-xs font-medium text-stone-800 dark:text-stone-200 transition-all cursor-pointer shadow-2xs active:scale-95"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Messages */}
        {messages.map((msg) => {
          if (msg.sender === "user") {
            return (
              <div key={msg.id} className="flex justify-end">
                <div className="max-w-[85%] sm:max-w-[78%] rounded-2xl px-3.5 py-2 bg-orange-600 dark:bg-orange-500 text-white dark:text-stone-950 shadow-xs rounded-tr-xs">
                  <p className="text-xs sm:text-[13px] font-medium leading-relaxed whitespace-pre-wrap">
                    {msg.text}
                  </p>
                </div>
              </div>
            );
          }

          // Assistant Response Card
          const cleanText = msg.text.trim();
          const isNotFound =
            cleanText.toLowerCase() === "no data found" ||
            cleanText.includes("No data found") ||
            cleanText.includes("Not found in verified document embeddings");
          const isFullCopied = copiedFullId === msg.id;

          return (
            <div key={msg.id} className="flex justify-start w-full">
              <div className="w-full sm:max-w-[88%] rounded-2xl p-3.5 sm:p-4 bg-white dark:bg-[#1C1510] border border-orange-200/80 dark:border-[#2D2118] shadow-xs rounded-tl-xs">
                {/* Header: Answer + Strategy badge + Copy action */}
                <div className="flex items-center justify-between pb-1.5 mb-2 border-b border-orange-100/70 dark:border-[#261B13]">
                  <div className="flex items-center gap-1.5 text-stone-700 dark:text-stone-300">
                    <Sparkles className="w-3.5 h-3.5 text-orange-500" />
                    <span className="text-xs font-semibold font-sans">
                      Answer
                    </span>
                    {msg.strategyUsed === 'fast_profile_cache' && (
                      <span className="text-[9px] font-mono font-bold bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20 px-1.5 py-0.2 rounded">
                        ⚡ profile cache
                      </span>
                    )}
                    {msg.strategyUsed === 'multi_hop_investigator' && (
                      <span className="text-[9px] font-mono font-bold bg-purple-500/10 text-purple-700 dark:text-purple-400 border border-purple-500/20 px-1.5 py-0.2 rounded">
                        🧠 multi-hop verified
                      </span>
                    )}
                    {msg.strategyUsed === 'interactive_clarification' && (
                      <span className="text-[9px] font-mono font-bold bg-blue-500/10 text-blue-700 dark:text-blue-400 border border-blue-500/20 px-1.5 py-0.2 rounded">
                        🤝 interactive choice
                      </span>
                    )}
                  </div>

                  {!isNotFound && (
                    <button
                      type="button"
                      onClick={() => handleCopyFull(msg.id, msg.text)}
                      title="Copy full answer text"
                      className="inline-flex items-center gap-1 text-[11px] font-mono text-stone-500 hover:text-orange-600 dark:text-stone-400 dark:hover:text-orange-300 transition-colors cursor-pointer px-1.5 py-0.5 rounded hover:bg-orange-50 dark:hover:bg-[#251A12]"
                    >
                      {isFullCopied ? (
                        <>
                          <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
                          <span className="text-emerald-600 dark:text-emerald-400 font-bold">
                            Copied!
                          </span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3 h-3" />
                          <span>Copy</span>
                        </>
                      )}
                    </button>
                  )}
                </div>

                {/* Content: Shown ONCE */}
                {isNotFound ? (
                  <div className="text-xs text-stone-500 dark:text-stone-400 py-1 font-medium font-mono">
                    No data found
                  </div>
                ) : (
                  <div className="text-xs sm:text-[13px] text-stone-900 dark:text-stone-100 leading-relaxed whitespace-pre-wrap">
                    {renderFormattedText(msg.text)}
                  </div>
                )}

                {/* Interactive Clarification Option Buttons (1-click choice) */}
                {msg.interactiveOptions && msg.interactiveOptions.length > 0 && (
                  <div className="mt-3 pt-2.5 border-t border-orange-100/70 dark:border-[#261B13] space-y-1.5">
                    <p className="text-[10px] font-mono text-stone-500 dark:text-stone-400 font-semibold">
                      Select an option:
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {msg.interactiveOptions.map((opt) => (
                        <button
                          key={opt.id}
                          type="button"
                          onClick={() => onSendMessage(opt.prompt_payload || opt.label)}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono font-semibold bg-orange-500/10 hover:bg-orange-600 hover:text-white text-orange-700 dark:text-orange-400 dark:hover:bg-orange-500 dark:hover:text-stone-950 border border-orange-500/25 transition-all cursor-pointer shadow-2xs active:scale-95"
                        >
                          <span>{opt.label}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Connected Source Documents (Top 1-2 only) */}
                {!isNotFound && msg.sources && msg.sources.length > 0 && (
                  <div className="mt-2.5 pt-2 border-t border-orange-100/70 dark:border-[#261B13] flex items-center flex-wrap gap-1.5">
                    <span className="text-[10px] font-mono text-stone-400">Source:</span>
                    {msg.sources.slice(0, 2).map((src, idx) => (
                      <button
                        type="button"
                        key={idx}
                        onClick={() => onOpenDocument?.(src)}
                        title={`Click to view ${src} in vault`}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-mono font-medium bg-orange-500/10 hover:bg-orange-500/20 active:scale-95 text-orange-700 dark:text-orange-300 border border-orange-500/20 transition-all cursor-pointer group shadow-2xs"
                      >
                        <FileText className="w-2.5 h-2.5 opacity-80" />
                        <span className="underline decoration-orange-400/50 underline-offset-2">
                          @va/{src}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })}

        {/* Loading Indicator */}
        {isLoading && (
          <div className="flex items-center space-x-2 text-xs text-stone-600 dark:text-stone-400 bg-white dark:bg-[#1C1510] px-3.5 py-2 rounded-xl border border-orange-200/80 dark:border-[#2D2118] w-fit shadow-xs font-mono">
            <span className="flex space-x-1">
              <span
                className="w-1.5 h-1.5 rounded-full bg-orange-500 animate-bounce"
                style={{ animationDelay: "0ms" }}
              />
              <span
                className="w-1.5 h-1.5 rounded-full bg-orange-500 animate-bounce"
                style={{ animationDelay: "150ms" }}
              />
              <span
                className="w-1.5 h-1.5 rounded-full bg-orange-500 animate-bounce"
                style={{ animationDelay: "300ms" }}
              />
            </span>
            <span className="text-[11px] font-medium text-stone-500 dark:text-stone-400">
              searching vault...
            </span>
          </div>
        )}
        {/* Mobile bottom clearance spacer so messages scroll cleanly above fixed input bar */}
        <div className="h-16 md:hidden shrink-0" />
        <div ref={messagesEndRef} />
      </div>

      {/* Writing Slit: Fixed at Bottom on Top of Mobile Navigation Bar in Mobile View */}
      <div className="fixed md:static bottom-[calc(3.5rem+env(safe-area-inset-bottom,0px))] md:bottom-auto left-0 right-0 z-30 md:z-auto bg-[#FFFCF9]/95 md:bg-white dark:bg-[#140E0A]/95 md:dark:bg-[#1C1510] backdrop-blur-md border-t border-orange-200/80 dark:border-[#2D2118] p-2.5 shadow-sm">
        {/* Input Field Slit with "Ask" button */}
        <form
          onSubmit={handleSubmit}
          className="relative flex items-center max-w-2xl mx-auto w-full px-2 md:px-0"
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about your documents..."
            className="w-full bg-white dark:bg-[#1C1510] border border-orange-200/90 dark:border-[#382A20] rounded-xl pl-3.5 pr-20 py-2.5 md:py-2 text-[14px] md:text-xs text-stone-900 dark:text-white placeholder-stone-400 focus:outline-none focus:border-orange-500 focus:ring-1 focus:ring-orange-500/20 shadow-xs transition-all font-sans"
          />
          <button
            type="submit"
            disabled={!input.trim() || isLoading}
            className="absolute right-3.5 md:right-1.5 top-1/2 -translate-y-1/2 px-3 py-1.5 md:py-1 bg-orange-600 hover:bg-orange-700 text-white dark:bg-orange-500 dark:hover:bg-orange-400 dark:text-stone-950 disabled:opacity-30 rounded-lg text-xs font-mono font-semibold transition-all hover:opacity-90 cursor-pointer flex items-center space-x-1 shrink-0 shadow-xs"
          >
            <span>{isLoading ? "Searching..." : "Ask"}</span>
          </button>
        </form>
      </div>
    </div>
  );
};
