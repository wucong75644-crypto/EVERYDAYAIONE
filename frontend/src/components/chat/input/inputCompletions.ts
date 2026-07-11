export const ECOM_TAB_COMPLETIONS: Record<string, string> = {
  "淘": "淘宝", "京": "京东", "拼": "拼多多", "抖": "抖音", "小红": "小红书",
  "白底": "白底主图 800×800", "场景": "场景图 800×800",
  "详情": "详情页 750×宽", "竖": "竖图 750×1000",
  "极简": "极简风格", "网感": "网感风格", "种草": "种草风格",
  "奢华": "高端奢华风格", "清新": "清新自然风格",
  "国潮": "国潮风格", "复古": "复古文艺风格", "暖": "暖调生活风格",
};

export const ECOM_TAB_KEYS_SORTED = Object.keys(ECOM_TAB_COMPLETIONS)
  .sort((a, b) => b.length - a.length);
