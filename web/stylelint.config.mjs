/** V3 门禁：禁裸 hex。tokens.css 是唯一允许 hex 的文件（token 来源）。
 *  记法规则（rgba/alpha 百分数/@theme）关闭：令牌值须与原型 :root 逐字一致（V-2 闸口优先）。 */
export default {
  extends: ['stylelint-config-standard'],
  rules: {
    'color-no-hex': true,
    'selector-class-pattern': null,
    'custom-property-pattern': null,
    'no-descending-specificity': null,
    'color-function-notation': null,
    'color-function-alias-notation': null,
    'alpha-value-notation': null,
    'declaration-empty-line-before': null,
    'at-rule-no-unknown': [true, { ignoreAtRules: ['theme', 'import', 'tailwind'] }],
    'at-rule-empty-line-before': null,
    'import-notation': null,
    'value-keyword-case': null,
    'color-hex-length': null,
    'media-feature-range-notation': null,
    'declaration-block-single-line-max-declarations': null,
    'rule-empty-line-before': null,
  },
  overrides: [
    {
      files: ['src/theme/tokens.css'],
      rules: { 'color-no-hex': null },
    },
  ],
  allowEmptyInput: true,
};
