module.exports = {
	extends: ["@commitlint/config-conventional"],
	rules: {
		// Fleet standard commit types — adds `security` to conventional base
		"type-enum": [
			2,
			"always",
			[
				"build",
				"chore",
				"ci",
				"docs",
				"feat",
				"fix",
				"perf",
				"refactor",
				"revert",
				"style",
				"test",
				"security",
			],
		],
	},
};
