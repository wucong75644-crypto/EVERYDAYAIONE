import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { generateRequirementSuggestions } from '../services/ecomRequirement';
import { toApiRequestError } from '../services/api';
import type { DetailGenerationForm } from '../types/detailPage';
import type {
  RequirementAssistResult,
  RequirementSuggestionId,
} from '../types/ecomRequirement';


type AssistStatus = 'idle' | 'loading' | 'success' | 'error';
type SuggestionDrafts = Record<RequirementSuggestionId, string>;

const emptyDrafts = (): SuggestionDrafts => ({ selling_point: '', scene: '', creative: '' });

interface RequestSource {
  projectId: string;
  form: DetailGenerationForm;
}


export function useDetailRequirementAssist() {
  const [isOpen, setIsOpen] = useState(false);
  const [status, setStatus] = useState<AssistStatus>('idle');
  const [result, setResult] = useState<RequirementAssistResult | null>(null);
  const [selectedId, setSelectedId] = useState<RequirementSuggestionId>('selling_point');
  const [drafts, setDrafts] = useState<SuggestionDrafts>(emptyDrafts);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<RequestSource | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const requestVersionRef = useRef(0);

  const requestSuggestions = useCallback(async (resetResult: boolean) => {
    const source = sourceRef.current;
    if (!source) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const requestVersion = ++requestVersionRef.current;
    setStatus('loading');
    setError(null);
    if (resetResult) {
      setResult(null);
      setDrafts(emptyDrafts());
      setSelectedId('selling_point');
    }
    try {
      const response = await generateRequirementSuggestions(
        source.projectId,
        source.form,
        controller.signal,
      );
      if (controller.signal.aborted || requestVersion !== requestVersionRef.current) return;
      const nextDrafts = emptyDrafts();
      for (const suggestion of response.data.suggestions) {
        nextDrafts[suggestion.id] = suggestion.brief_markdown;
      }
      setResult(response.data);
      setDrafts(nextDrafts);
      setSelectedId(response.data.suggestions[0]?.id ?? 'selling_point');
      setStatus('success');
    } catch (requestError) {
      if (controller.signal.aborted || requestVersion !== requestVersionRef.current) return;
      setError(toApiRequestError(requestError).message);
      setStatus('error');
    } finally {
      if (requestVersion === requestVersionRef.current) controllerRef.current = null;
    }
  }, []);

  const open = useCallback(async (projectId: string, form: DetailGenerationForm) => {
    sourceRef.current = { projectId, form: { ...form } };
    setIsOpen(true);
    await requestSuggestions(true);
  }, [requestSuggestions]);

  const regenerate = useCallback(async () => {
    await requestSuggestions(false);
  }, [requestSuggestions]);

  const close = useCallback(() => {
    requestVersionRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    setIsOpen(false);
  }, []);

  const updateDraft = useCallback((id: RequirementSuggestionId, value: string) => {
    setDrafts((current) => ({ ...current, [id]: value }));
  }, []);

  useEffect(() => () => {
    requestVersionRef.current += 1;
    controllerRef.current?.abort();
  }, []);

  const selectedBrief = useMemo(() => drafts[selectedId], [drafts, selectedId]);

  return {
    isOpen,
    status,
    isLoading: status === 'loading',
    result,
    selectedId,
    selectedBrief,
    drafts,
    error,
    open,
    close,
    regenerate,
    selectSuggestion: setSelectedId,
    updateDraft,
  };
}
