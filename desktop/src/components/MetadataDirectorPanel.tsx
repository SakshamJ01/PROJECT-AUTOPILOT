/**
 * Phase 5 -- Metadata Director & Thumbnail Generator Panel
 * Generates title, description, hashtags, and composite 9:16 thumbnail metadata
 * independently from spoken narration.
 */
import { useState } from 'react';
import type { JobInspect } from '../api/types';

interface MetadataOutput {
  title: string;
  description: string;
  hashtags: string[];
  thumbnail_concept: string;
}

function generateMetadataFromJob(inspect: JobInspect): MetadataOutput {
  const manifest = (inspect.manifest ?? inspect.job ?? {}) as Record<string, unknown>;
  const topic = String(manifest.topic ?? inspect.job_id ?? 'Unknown Topic');

  try {
    const raw = manifest.metadata_json;
    if (typeof raw === 'string') {
      const parsed = JSON.parse(raw) as Partial<MetadataOutput>;
      if (parsed.title) return parsed as MetadataOutput;
    }
  } catch { /* ignore */ }

  // Synthetic fallback
  const words = topic.split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase());
  const title = words.slice(0, 8).join(' ');
  return {
    title: title + ' #Shorts',
    description: 'An AI-generated short-form educational video about: ' + topic + '. Produced by Project Autopilot.',
    hashtags: ['#Shorts', '#DidYouKnow', '#' + words[0], '#Facts', '#LearnOnTikTok'],
    thumbnail_concept: 'Bold yellow text "' + (words[0] ?? 'FACT') + '" on dark background with subject imagery',
  };
}

export default function MetadataDirectorPanel({ inspect }: { inspect: JobInspect }) {
  const [meta, setMeta] = useState<MetadataOutput>(() => generateMetadataFromJob(inspect));
  const [copied, setCopied] = useState<string | null>(null);

  const copy = (field: string, text: string) => {
    navigator.clipboard.writeText(text).catch(() => {});
    setCopied(field);
    setTimeout(() => setCopied(null), 1800);
  };

  return (
    <div className='card metadata-director-card'>
      <div className='card-header'>
        <span>Metadata Director & Thumbnail</span>
        <span className='muted small'>Independent from narration</span>
      </div>
      <div className='card-body'>

        {/* Title */}
        <div className='metadata-field'>
          <label htmlFor='meta-title-input' className='metadata-field-label'>YouTube/TikTok Title</label>
          <div style={{ display: 'flex', gap: '8px' }}>
            <input
              id='meta-title-input'
              className='text-input'
              value={meta.title}
              onChange={e => setMeta(m => ({ ...m, title: e.target.value }))}
              maxLength={100}
              aria-label='Video title'
            />
            <button
              id='btn-copy-title'
              className='ghost-btn'
              onClick={() => copy('title', meta.title)}
              style={{ flexShrink: 0 }}
            >
              {copied === 'title' ? '\u2713 Copied' : 'Copy'}
            </button>
          </div>
          <div className='muted small' style={{ textAlign: 'right' }}>{meta.title.length}/100</div>
        </div>

        {/* Description */}
        <div className='metadata-field'>
          <label htmlFor='meta-desc-input' className='metadata-field-label'>Description</label>
          <div style={{ display: 'flex', gap: '8px' }}>
            <textarea
              id='meta-desc-input'
              className='text-input'
              rows={3}
              value={meta.description}
              onChange={e => setMeta(m => ({ ...m, description: e.target.value }))}
              maxLength={5000}
              aria-label='Video description'
              style={{ resize: 'vertical', flex: 1 }}
            />
            <button
              id='btn-copy-description'
              className='ghost-btn'
              onClick={() => copy('description', meta.description)}
              style={{ flexShrink: 0, alignSelf: 'flex-start' }}
            >
              {copied === 'description' ? '\u2713 Copied' : 'Copy'}
            </button>
          </div>
        </div>

        {/* Hashtags */}
        <div className='metadata-field'>
          <label className='metadata-field-label'>Hashtags</label>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center' }}>
            {meta.hashtags.map((tag, i) => (
              <span key={i} className='hashtag-badge'>
                {tag}
                <button
                  className='hashtag-remove'
                  aria-label={'Remove ' + tag}
                  onClick={() => setMeta(m => ({ ...m, hashtags: m.hashtags.filter((_, j) => j !== i) }))}
                >
                  \u00d7
                </button>
              </span>
            ))}
            <button
              id='btn-copy-hashtags'
              className='ghost-btn'
              onClick={() => copy('hashtags', meta.hashtags.join(' '))}
            >
              {copied === 'hashtags' ? '\u2713 Copied' : 'Copy all'}
            </button>
          </div>
        </div>

        {/* Thumbnail concept */}
        <div className='metadata-field'>
          <label htmlFor='meta-thumbnail-input' className='metadata-field-label'>Thumbnail Concept (9:16)</label>
          <div style={{ display: 'flex', gap: '8px' }}>
            <textarea
              id='meta-thumbnail-input'
              className='text-input'
              rows={2}
              value={meta.thumbnail_concept}
              onChange={e => setMeta(m => ({ ...m, thumbnail_concept: e.target.value }))}
              aria-label='Thumbnail concept description'
              style={{ resize: 'vertical', flex: 1 }}
            />
          </div>
        </div>

        <div className='thumbnail-preview-box' aria-label='Thumbnail preview (9:16 ratio)'>
          <div className='thumbnail-preview-inner'>
            <div className='thumbnail-preview-title'>{meta.title.replace(/#\w+/g, '').trim().slice(0, 40)}</div>
            <div className='thumbnail-preview-tag muted small'>9:16 · 1080x1920</div>
          </div>
        </div>

      </div>
    </div>
  );
}
