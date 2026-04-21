// src/App.tsx
import { useState, useRef, useEffect } from 'react';
import './App.css';

const API_BASE = `http://${import.meta.env.VITE_SERVER_IP}:8000`;
const API_STREAM = `${API_BASE}/stream_message`;

interface Message {
	id: string;
	text: string;
	sender: 'user' | 'bot';
	timestamp: Date;
}

const App: React.FC = () => {
	const [messages, setMessages] = useState<Message[]>([
		{
			id: '1',
			text: 'Hello! How can I help you today?',
			sender: 'bot',
			timestamp: new Date(),
		}
	]);

	const [inputValue, setInputValue] = useState('');
	const [isLoading, setIsLoading] = useState(false);
	const [isAudio, setIsAudio] = useState(false);
	const messagesEndRef = useRef<HTMLDivElement>(null);
	const handleToggle = () => {
		setIsAudio(!isAudio);
	};

	useEffect(() => {
		const apiBase = API_BASE;
		fetch(`${apiBase}/conversation`)
			.then(r => r.json())
			.then(data => {
				if (data.messages && data.messages.length > 0) {
					const loaded: Message[] = data.messages.map((m: { role: string; content: string; created_at: string }) => ({
						id: m.created_at + m.role,
						text: m.content,
						sender: m.role === 'user' ? 'user' : 'bot',
						timestamp: new Date(m.created_at),
					}));
					setMessages(loaded);
				}
			})
			.catch(() => {});
	}, []);

	const handleClearConversation = async () => {
		const apiBase = API_BASE;
		await fetch(`${apiBase}/conversation`, { method: 'DELETE' }).catch(() => {});
		setMessages([{
			id: Date.now().toString(),
			text: 'Hello! How can I help you today?',
			sender: 'bot',
			timestamp: new Date(),
		}]);
	};

	const scrollToBottom = () => {
		messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
	};

	useEffect(() => {
		scrollToBottom();
	}, [messages]);

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!inputValue.trim() || isLoading) return;

		// Add user message
		const userMessage: Message = {
			id: Date.now().toString(),
			text: inputValue,
			sender: 'user',
			timestamp: new Date(),
		};

		setMessages(prev => [...prev, userMessage]);
		setInputValue('');
		setIsLoading(true);

		const abortController = new AbortController();
		const timeoutId = setTimeout(() => abortController.abort(), 10 * 60 * 1000);

		try {
			// Always use streaming - audio flag tells backend to also generate TTS
			const apiUrl = API_STREAM;
			const response = await fetch(apiUrl, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ message: inputValue, audio: isAudio }),
				signal: abortController.signal,
			});
			if (!response.ok) throw new Error(`HTTP error! Status: ${response.status}`);

			const botMessageId = Date.now().toString();
			const botMessage: Message = {
				id: botMessageId,
				text: '',
				sender: 'bot',
				timestamp: new Date(),
			};
			setMessages(prev => [...prev, botMessage]);

			const reader = response.body!.getReader();
			const decoder = new TextDecoder();
			let buffer = '';

			while (true) {
				const { done, value } = await reader.read();
				if (done) break;

				buffer += decoder.decode(value, { stream: true });
				const lines = buffer.split('\n');
				buffer = lines.pop() || '';

				for (const line of lines) {
					if (!line.startsWith('data: ')) continue;
					const jsonStr = line.slice(6);
					try {
						const parsed = JSON.parse(jsonStr);
						if (parsed.done) break;
						if (parsed.content) {
							setMessages(prev =>
								prev.map(msg =>
									msg.id === botMessageId
										? { ...msg, text: msg.text + parsed.content }
										: msg
								)
							);
						}
					} catch {
						// skip malformed JSON
					}
				}
			}
		} catch (error) {
			console.error('Error sending message:', error);
			const isTimeout = error instanceof DOMException && error.name === 'AbortError';
			const errorMessage: Message = {
				id: Date.now().toString(),
				text: isTimeout
					? 'Request timed out. The server took too long to respond.'
					: 'Sorry, I encountered an error. Please try again.',
				sender: 'bot',
				timestamp: new Date(),
			};
			setMessages(prev => [...prev, errorMessage]);
		} finally {
			clearTimeout(timeoutId);
			setIsLoading(false);
		}
	};

	return (
		<div className="app">
			<div className="chat-container">
				<div className="messages-container">
					{messages.map((message) => (
						<div
							key={message.id}
							className={`message ${message.sender}`}
						>
							<div className="message-text">{message.text}</div>
							<div className="message-time">
								{message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
							</div>
						</div>
					))}
					{isLoading && (
						<div className="message bot typing">
							<div className="typing-indicator">
								<span></span>
								<span></span>
								<span></span>
							</div>
						</div>
					)}
					<div ref={messagesEndRef} />
				</div>

				<form className="input-container" onSubmit={handleSubmit}>
					<input
						type="text"
						value={inputValue}
						onChange={(e) => setInputValue(e.target.value)}
						placeholder="Type your message..."
						disabled={isLoading}
					/>
					<button type="submit" disabled={isLoading || !inputValue.trim()}>
						Send
					</button>
					<button type="button" onClick={handleClearConversation} disabled={isLoading}>
						New Conversation
					</button>
					<input
						type="checkbox"
						checked={isAudio}
						onChange={handleToggle}
					/>
					Audio Mode
				</form>
			</div>
		</div>
	);
};

export default App;
