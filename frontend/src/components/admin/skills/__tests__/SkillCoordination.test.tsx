import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { SkillCoordination } from '../SkillCoordination';
import type { DraftContent } from '../../../../services/skillAdmin';

const content: DraftContent={description:'method',body:'original body',
  catalog_metadata:{tool_policy:'platform',model_selectable:false},assets:[]};

it('does not silently classify or rewrite an existing method', () => {
  const change=vi.fn();
  render(<SkillCoordination content={content} busy={false} onChange={change} />);
  expect(screen.getByRole('combobox')).toHaveValue('legacy');
  expect(change).not.toHaveBeenCalled();
});

it('uses a supported execution profile without granting tools or changing the body', () => {
  const change=vi.fn();
  render(<SkillCoordination content={content} busy={false} onChange={change} />);
  fireEvent.change(screen.getByRole('combobox'),{target:{value:'inline'}});
  const next=change.mock.calls[0][0];
  expect(next.body).toBe(content.body);
  expect(next.catalog_metadata.tool_policy).toBe('platform');
  expect(next.catalog_metadata.model_selectable).toBe(false);
  expect(next.catalog_metadata.coordination.claims[0].method).toBe('ecommerce.inline');
  expect(content.catalog_metadata).not.toHaveProperty('coordination');
});

it('recognizes a roundtripped profile with empty default dependencies', () => {
  const value={...content,catalog_metadata:{...content.catalog_metadata,coordination:{
    kind:'guidance',claims:[],requires:[],version:1,
  }}};
  render(<SkillCoordination content={value} busy={false} onChange={vi.fn()} />);
  expect(screen.getByRole('combobox')).toHaveValue('guidance');
});

it('keeps an unknown stored contract until the author explicitly chooses another', () => {
  const change=vi.fn();
  const value={...content,catalog_metadata:{...content.catalog_metadata,coordination:{version:2,kind:'future'}}};
  render(<SkillCoordination content={value} busy={true} onChange={change} />);
  expect(screen.getByRole('combobox')).toHaveValue('custom');
  expect(screen.getByRole('combobox')).toBeDisabled();
  expect(change).not.toHaveBeenCalled();
});
