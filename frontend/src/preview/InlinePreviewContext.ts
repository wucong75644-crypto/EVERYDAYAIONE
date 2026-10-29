import {createContext} from 'react';
/** An embedded document must not portal, lock page scrolling, or own global Escape. */
export const InlinePreviewContext=createContext<{onOpen:()=>void}|null>(null);
