# Drupal Framework for NicheBench

This directory contains the Drupal framework implementation for NicheBench, featuring framework-specific AI evaluation tasks.

## Structure

```text
drupal/
├── data/           # Private test data (Git submodule)
│   ├── bug_fixing/
│   ├── code_generation/
│   └── quiz/
├── prompts/        # Public evaluation prompts
├── samples/        # Template files for creating new tests
│   ├── code_generation.sample.yml
│   ├── quiz.sample.yml
│   └── bug_fixing.sample.yml
└── registry.py     # Framework registration
```

## Test Data Access

The test data in the `data/` directory is stored in a **private repository** as a Git submodule to prevent it from being crawled and incorporated into AI training datasets.

### Why Private Data?

- **Benchmark Integrity**: Keeps test cases novel and prevents models from being trained on evaluation data
- **Quality Control**: Maintains the challenging nature of our benchmarks
- **Collaborative Development**: Allows controlled access for contributors while protecting the test suite

### Public vs Private Components

| Component | Visibility | Rationale |
|-----------|------------|-----------|
| **Framework Structure** | Public | Encourages adoption and contribution |
| **Evaluation Prompts** | Public | Promotes transparency in evaluation criteria |
| **Test Data** | Private | Preserves benchmark integrity |

## Getting Access to Test Data

The test data repository is private. To access it:

1. **For Contributors**: Request access to collaborate on test case development
2. **For Researchers**: Contact maintainers to discuss access for legitimate research
3. **For Users**: The data will be automatically available when you clone with submodules

## Setting Up

When cloning the main repository, use:

```bash
# Clone with submodules
git clone --recursive git@github.com:HumanFace-Tech/nichebench.git

# Or if already cloned, initialize submodules
git submodule update --init --recursive
```

## Development Workflow

### Adding New Test Cases

1. Ensure you have access to the private data repository
2. Add your test cases to the appropriate directory in `data/`
3. Follow the existing naming conventions (`quiz_XXX.yaml`, etc.)
4. Commit and push to the data repository
5. Update the submodule reference in the main repository

### Updating Test Data

```bash
# Navigate to the data submodule
cd src/nichebench/frameworks/drupal/data

# Pull latest changes
git pull origin main

# Go back to main repository
cd ../../../../

# Commit the submodule update
git add src/nichebench/frameworks/drupal/data
git commit -m "Update Drupal test data"
```

## Creating New Test Cases

The `samples/` directory contains template files to help you create new test cases:

- **`code_generation.sample.yml`**: Template for complex Drupal development tasks
- **`quiz.sample.yml`**: Template for multiple choice knowledge questions
- **`bug_fixing.sample.yml`**: Template for real-world bug scenarios

### Test Case Guidelines

**Where Test Cases Live:**

- All actual test cases go in the **private data repository** under `data/`
- Use the samples as templates but **never commit sample files to the data repo**
- Follow naming conventions: `quiz_001.yaml`, `codegen_001.yaml`, `bug_001.yaml`

**Writing Effective Tests:**

- **Context**: Provide rich background (Drupal version, modules, business requirements)
- **Prompts**: Should lead AI toward a specific solution path without being too obvious
- **Judge Checklists**: 5-15 criteria that reflect what you guided the AI toward
- **Judge Notes**: Background knowledge for the LLM judge when domain expertise is needed

**Quality Standards:**

- Prompts should be neither vague nor overly precise
- Avoid expecting knowledge that wasn't provided in the context
- Test real-world scenarios that Drupal developers actually encounter
- Ensure judge criteria are fair and based on the given information

## Test Types

- **Quiz**: Multiple choice questions testing Drupal knowledge
- **Code Generation**: Complex multi-file Drupal development tasks
- **Bug Fixing**: Real-world Drupal bug scenarios requiring patches

All evaluations use **LLM-as-a-Judge** with dynamic criteria, providing nuanced 3-value scoring (pass/partial/fail).

## Philosophy

We believe in **open frameworks with private data**:

- ✅ **Framework code**: Public and open source
- ✅ **Evaluation prompts**: Public for transparency
- 🔒 **Test data**: Private to maintain benchmark integrity

This approach enables collaborative development while protecting evaluation quality.
