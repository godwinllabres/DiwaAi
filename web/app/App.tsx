import { useState, useRef, useEffect, useMemo } from "react";
import {
  Send,
  GraduationCap,
  BookOpen,
  Users,
  MapPin,
  FileText,
  Calendar,
  Home,
  BarChart3,
  AlertCircle,
  Phone,
} from "lucide-react";
import { ChatMessage, type FeedbackSubmission } from "./components/ChatMessage";
import { QuickActionButton } from "./components/QuickActionButton";
import { CategoryCard } from "./components/CategoryCard";
import { TypingIndicator } from "./components/TypingIndicator";
import { AdminDashboard } from "./components/AdminDashboard";
import { motion, AnimatePresence } from "motion/react";
import { api, type MapData } from "./lib/api";
import { getUserId, getSessionId } from "./lib/ids";
import {
  getQuickActionTopics,
  getStarterTopics,
  getSuggestedTopics,
  getSuggestionCopy,
  type TopicSuggestion,
} from "./lib/topics";

interface Message {
  id: number;
  text: string;
  isBot: boolean;
  timestamp: string;
  intent?: string;
  confidence?: number;
  messageId?: number;
  mapData?: MapData | null;
}

const ICON_MAP: Record<string, any> = {
  admissions: FileText,
  enrollment: Calendar,
  courses: BookOpen,
  programs: BookOpen,
  facilities: MapPin,
  campus: MapPin,
  contact: Phone,
  registrar: FileText,
  scholarship: GraduationCap,
  support: Users,
  services: Users,
  schedule: Calendar,
  tuition: FileText,
};

function pickIcon(tag: string) {
  const key = Object.keys(ICON_MAP).find((k) => tag.toLowerCase().includes(k));
  return key ? ICON_MAP[key] : BookOpen;
}

function timeNow() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function App() {
  const userId = useMemo(() => getUserId(), []);
  const sessionId = useMemo(() => getSessionId(), []);
  const quickActions = useMemo(() => getQuickActionTopics(), []);

  const [messages, setMessages] = useState<Message[]>([
    {
      id: 1,
      text: "Hi! I'm Sevi, the official chatbot of Cavite State University. I'm here to help you with admissions, enrollment, courses, scholarships, campus facilities, and more. What can I help you with today?",
      isBot: true,
      timestamp: timeNow(),
    },
  ]);
  const [inputValue, setInputValue] = useState("");
  const [showCategories, setShowCategories] = useState(true);
  const [isTyping, setIsTyping] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [showAdmin, setShowAdmin] = useState(false);
  const [categories, setCategories] = useState<TopicSuggestion[]>(() => getStarterTopics(4));
  const [categoryHeading, setCategoryHeading] = useState("Start with a topic");
  const [categorySubheading, setCategorySubheading] = useState(
    "Tap a suggested question to get a faster answer.",
  );
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const idCounterRef = useRef(2);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping]);

  const pushMessage = (m: Omit<Message, "id" | "timestamp"> & { timestamp?: string }) => {
    const id = idCounterRef.current++;
    setMessages((prev) => [
      ...prev,
      { id, timestamp: m.timestamp ?? timeNow(), ...m },
    ]);
    return id;
  };

  const showSuggestedTopics = (topics: TopicSuggestion[], heading: string, subheading: string) => {
    setCategories(topics);
    setCategoryHeading(heading);
    setCategorySubheading(subheading);
    setShowCategories(topics.length > 0);
  };

  const sendToApi = async (text: string) => {
    setIsTyping(true);
    setApiError(null);

    try {
      const res = await api.chat({
        message: text,
        user_id: userId,
        session_id: sessionId,
      });

      pushMessage({
        text: res.response,
        isBot: true,
        intent: res.intent,
        confidence: res.confidence,
        messageId: res.message_id ?? undefined,
        mapData: res.map_data ?? null,
      });

      const suggestedTopics = getSuggestedTopics({
        answer: res.response,
        confidence: res.confidence,
        intent: res.intent,
        question: text,
      });
      const suggestionCopy = getSuggestionCopy(res.intent, res.confidence);

      showSuggestedTopics(
        suggestedTopics,
        suggestionCopy.heading,
        suggestionCopy.subheading,
      );
    } catch (e: any) {
      setApiError(e?.message ?? "Network error");
      pushMessage({
        text: "I'm having trouble reaching the server right now. Please try again in a moment, or contact CvSU at (046) 415-0010.",
        isBot: true,
      });

      showSuggestedTopics(
        getStarterTopics(4),
        "Start with a topic",
        "You can still try one of these common CvSU questions.",
      );
    } finally {
      setIsTyping(false);
    }
  };

  const handleSend = () => {
    const text = inputValue.trim();
    if (!text || isTyping) return;

    pushMessage({ text, isBot: false });
    setInputValue("");
    setShowCategories(false);
    sendToApi(text);
  };

  const handleQuickAction = (topic: TopicSuggestion) => {
    if (isTyping) return;

    setShowCategories(false);
    pushMessage({ text: topic.prompt, isBot: false });
    sendToApi(topic.prompt);
  };

  const handleFeedback = async (
    messageId: number,
    intent: string | undefined,
    submission: FeedbackSubmission,
  ) => {
    try {
      await api.submitFeedback({
        message_id: messageId,
        user_id: userId,
        session_id: sessionId,
        intent,
        helpful: submission.helpful,
        rating: submission.helpful ? 5 : 2,
        reason: submission.reason,
        comment: submission.comment,
      });
    } catch {
      // silent
    }
  };

  const handleStartOver = () => {
    setMessages([messages[0]]);
    setShowCategories(true);
    setInputValue("");
    setApiError(null);
    setCategories(getStarterTopics(4));
    setCategoryHeading("Start with a topic");
    setCategorySubheading("Tap a suggested question to get a faster answer.");
    idCounterRef.current = 2;
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="size-full bg-gradient-to-br from-green-50 via-white to-green-50 flex items-center justify-center p-4">
      <div className="relative w-full max-w-4xl h-[95vh] bg-white rounded-3xl shadow-2xl overflow-hidden flex flex-col">
        <div className="bg-gradient-to-r from-green-600 to-green-700 px-6 py-5 flex items-center gap-4 flex-shrink-0">
          <div className="w-14 h-14 rounded-full bg-white flex items-center justify-center">
            <GraduationCap className="w-8 h-8 text-green-600" />
          </div>
          <div className="flex-1">
            <h1 className="text-xl text-white mb-1">Sevi</h1>
            <p className="text-sm text-green-100">Your CvSU Virtual Assistant</p>
          </div>
          <button
            onClick={() => setShowAdmin(true)}
            className="w-10 h-10 rounded-full bg-white/20 hover:bg-white/30 flex items-center justify-center transition-colors"
            aria-label="Admin dashboard"
            title="Admin dashboard"
          >
            <BarChart3 className="w-5 h-5 text-white" />
          </button>
          <div className="w-3 h-3 rounded-full bg-green-300 animate-pulse" />
        </div>

        {apiError && (
          <div className="px-6 py-2 bg-amber-50 border-b border-amber-200 flex items-center gap-2 text-xs text-amber-800">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span>API unreachable: {apiError}. Make sure the chatbot server is running.</span>
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {messages.map((msg) => (
            <ChatMessage
              key={msg.id}
              message={msg.text}
              isBot={msg.isBot}
              timestamp={msg.timestamp}
              intent={msg.intent}
              confidence={msg.confidence}
              messageId={msg.messageId}
              mapData={msg.mapData}
              onFeedback={
                msg.messageId === undefined
                  ? undefined
                  : (submission) =>
                      handleFeedback(msg.messageId as number, msg.intent, submission)
              }
            />
          ))}

          {isTyping && <TypingIndicator />}

          <AnimatePresence>
            {showCategories && (
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -20 }}
                className="mt-6 space-y-3"
              >
                <div className="mb-4">
                  <p className="text-sm text-gray-900">{categoryHeading}</p>
                  <p className="text-sm text-gray-600 mt-1">{categorySubheading}</p>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {categories.map((topic) => (
                    <CategoryCard
                      key={topic.tag}
                      icon={pickIcon(topic.tag)}
                      title={topic.title}
                      description={topic.description}
                      onClick={() => handleQuickAction(topic)}
                    />
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <div ref={messagesEndRef} />
        </div>

        {!showCategories && (
          <div className="px-6 py-3 border-t border-gray-200 bg-gray-50">
            <div className="flex gap-2 overflow-x-auto pb-2">
              <QuickActionButton icon={Home} label="Start Over" onClick={handleStartOver} />
              {quickActions.map((topic) => (
                <QuickActionButton
                  key={topic.tag}
                  icon={pickIcon(topic.tag)}
                  label={topic.title}
                  onClick={() => handleQuickAction(topic)}
                />
              ))}
            </div>
          </div>
        )}

        <div className="p-6 border-t border-gray-200 bg-white flex-shrink-0">
          <div className="flex gap-3">
            <input
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyPress}
              placeholder={isTyping ? "Sevi is typing..." : "Type your question here..."}
              disabled={isTyping}
              className="flex-1 px-4 py-3 border-2 border-gray-200 rounded-xl focus:outline-none focus:border-green-600 transition-colors disabled:bg-gray-50"
            />
            <button
              onClick={handleSend}
              disabled={!inputValue.trim() || isTyping}
              className="w-12 h-12 bg-green-600 hover:bg-green-700 disabled:bg-gray-300 rounded-xl flex items-center justify-center transition-colors"
            >
              <Send className="w-5 h-5 text-white" />
            </button>
          </div>
          <p className="text-xs text-gray-500 mt-3 text-center">
            For urgent concerns, contact CvSU Main Campus: (046) 415-0010
          </p>
        </div>

        <AnimatePresence>
          {showAdmin && <AdminDashboard onClose={() => setShowAdmin(false)} />}
        </AnimatePresence>
      </div>
    </div>
  );
}
