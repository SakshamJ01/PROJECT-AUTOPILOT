/**
 * Phase 5 -- Creator Mode Panel
 * Simple creator controls (Topic, Voice, Visual Style, BGM)
 * separated from engineering knobs visible in Advanced mode.
 */
import { useState } from 'react';

export type CreatorModeValues = {
  topic: string;
  voice: string;
  visualStyle: string;
  bgmTone: string;
  channel: string;
};

export type AdvancedValues = {
  policy: string;
  profile: string;
  llmProvider: string;
};

const VOICE_OPTIONS = ['edge-tts (default)', 'kokoro-local', 'windows-sapi', 'mock'] as const;
const VISUAL_STYLE_OPTIONS = ['auto', 'cinematic', 'documentary', 'minimal', 'vibrant'] as const;
const BGM_TONE_OPTIONS = ['ambient', 'lo-fi', 'upbeat', 'dramatic', 'none'] as const;
const POLICY_OPTIONS = ['local_only', 'cheap_first', 'ollama', 'mock'] as const;
const PROFILE_OPTIONS = ['short_vertical', 'long_form', 'podcast'] as const;
const LLM_OPTIONS = ['ollama-local', 'gemini-flash', 'openrouter', 'mock'] as const;

export default function CreatorModePanel({
  onSubmit,
  isPending,
}: {
  onSubmit: (creator: CreatorModeValues, advanced: AdvancedValues) => void;
  isPending: boolean;
}) {
  const [mode, setMode] = useState<'creator' | 'advanced'>('creator');
  const [topic, setTopic] = useState('');
  const [voice, setVoice] = useState(VOICE_OPTIONS[0] as string);
  const [visualStyle, setVisualStyle] = useState(VISUAL_STYLE_OPTIONS[0] as string);
  const [bgmTone, setBgmTone] = useState(BGM_TONE_OPTIONS[0] as string);
  const [channel, setChannel] = useState('default');
  const [policy, setPolicy] = useState(POLICY_OPTIONS[0] as string);
  const [profile, setProfile] = useState(PROFILE_OPTIONS[0] as string);
  const [llmProvider, setLlmProvider] = useState(LLM_OPTIONS[0] as string);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!topic.trim()) return;
    onSubmit(
      { topic: topic.trim(), voice, visualStyle, bgmTone, channel },
      { policy, profile, llmProvider }
    );
  };

  return (
    <form className='creator-mode-panel' onSubmit={handleSubmit} aria-label='Creator mode production form'>
      <div className='creator-mode-header'>
        <div className='mode-toggle' role='group' aria-label='Production mode selector'>
          <button
            type='button'
            id='btn-creator-mode'
            className={'mode-toggle-btn' + (mode === 'creator' ? ' active' : '')}
            onClick={() => setMode('creator')}
          >
            Creator Mode
          </button>
          <button
            type='button'
            id='btn-advanced-mode'
            className={'mode-toggle-btn' + (mode === 'advanced' ? ' active' : '')}
            onClick={() => setMode('advanced')}
          >
            Advanced Settings
          </button>
        </div>
      </div>

      {/* Creator fields always visible */}
      <div className='creator-fields'>
        <label className='creator-field-full'>
          <span className='creator-field-label'>Topic</span>
          <input
            id='creator-topic-input'
            className='text-input'
            type='text'
            value={topic}
            onChange={e => setTopic(e.target.value)}
            placeholder='What should the video be about?'
            aria-label='Video topic'
            required
          />
        </label>

        <label>
          <span className='creator-field-label'>Voice</span>
          <select id='creator-voice-select' className='select-input' value={voice} onChange={e => setVoice(e.target.value)} aria-label='TTS voice'>
            {VOICE_OPTIONS.map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>

        <label>
          <span className='creator-field-label'>Visual Style</span>
          <select id='creator-visual-select' className='select-input' value={visualStyle} onChange={e => setVisualStyle(e.target.value)} aria-label='Visual style'>
            {VISUAL_STYLE_OPTIONS.map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>

        <label>
          <span className='creator-field-label'>BGM Tone</span>
          <select id='creator-bgm-select' className='select-input' value={bgmTone} onChange={e => setBgmTone(e.target.value)} aria-label='Background music tone'>
            {BGM_TONE_OPTIONS.map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>

        <label>
          <span className='creator-field-label'>Channel</span>
          <input id='creator-channel-input' className='text-input' type='text' value={channel} onChange={e => setChannel(e.target.value)} aria-label='Channel' />
        </label>
      </div>

      {/* Advanced fields only in advanced mode */}
      {mode === 'advanced' && (
        <div className='advanced-fields'>
          <div className='advanced-fields-label muted small'>Advanced Settings</div>
          <label>
            <span className='creator-field-label'>Policy</span>
            <select id='advanced-policy-select' className='select-input' value={policy} onChange={e => setPolicy(e.target.value)} aria-label='Production policy'>
              {POLICY_OPTIONS.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
          <label>
            <span className='creator-field-label'>Profile</span>
            <select id='advanced-profile-select' className='select-input' value={profile} onChange={e => setProfile(e.target.value)} aria-label='Production profile'>
              {PROFILE_OPTIONS.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
          <label>
            <span className='creator-field-label'>LLM Provider</span>
            <select id='advanced-llm-select' className='select-input' value={llmProvider} onChange={e => setLlmProvider(e.target.value)} aria-label='LLM provider'>
              {LLM_OPTIONS.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
        </div>
      )}

      <div className='start-form-actions'>
        <p className='muted small'>
          {mode === 'creator'
            ? 'Creator Mode: simple controls for topic, voice, style, and BGM.'
            : 'Advanced: full control over providers, policy, and profile.'}
        </p>
        <button
          id='btn-start-production'
          className='primary-btn'
          type='submit'
          disabled={!topic.trim() || isPending}
        >
          {isPending ? 'Starting\u2026' : '\u25b6 Start Production'}
        </button>
      </div>
    </form>
  );
}
