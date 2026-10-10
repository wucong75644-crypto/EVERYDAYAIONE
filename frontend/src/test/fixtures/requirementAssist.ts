import type { RequirementAssistResult, RequirementSuggestionsEnvelope } from '../../types/ecomRequirement';
export function assistDraft(): RequirementAssistResult {
  return {
    product_description:'红色存钱本，普通印刷，侧边搭扣',
    selling_points:[{feature:'红金主题',benefit:'送礼有仪式感',benefit_basis:'inferred'}],
    creative_requirements:[{topic:'背景',text:'米白背景，保留红金感觉',basis:'explicit'}],
    supplement_questions:[
      {question:'本体尺寸是多少？',why:'完善规格',can_skip:true},
      {question:'封面材质是什么？',why:'完善细节',can_skip:true},
    ],
  };
}
export function assistResponse(): RequirementSuggestionsEnvelope {
  return {success:true,data:assistDraft(),error:null,meta:{model:'kimi-k3',fallback_used:false,latency_ms:34000,project_version:2}};
}
