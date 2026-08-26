[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$failures = [System.Collections.Generic.List[string]]::new()

function Get-ProjectRelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$TargetPath
    )

    # Windows PowerShell 5 runs on a framework without Path.GetRelativePath.
    # Use the newer API when present and fall back to URI resolution otherwise.
    try {
        return [System.IO.Path]::GetRelativePath($BasePath, $TargetPath)
    }
    catch {
        $baseUri = New-Object System.Uri(($BasePath.TrimEnd('\') + '\'))
        $targetUri = New-Object System.Uri($TargetPath)
        return [System.Uri]::UnescapeDataString(
            $baseUri.MakeRelativeUri($targetUri).ToString()
        ).Replace('/', '\')
    }
}

$requiredFiles = @(
    'README.md',
    'AGENTS.md',
    'docs/core-principles.md',
    'docs/glossary.md',
    'docs/turn-protocol.md',
    'docs/event-specification.md',
    'docs/domain-model.md',
    'docs/context-memory-specification.md',
    'docs/consequence-specification.md',
    'docs/stage-0-acceptance.md',
    'docs/stage-0-validation-report.md',
    'docs/stage-3c-part2-validation-report.md',
    'docs/decisions/0001-frontend-stack.md',
    'docs/decisions/0002-backend-architecture.md'
)

foreach ($relativePath in $requiredFiles) {
    $absolutePath = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
        $failures.Add("Missing required file: $relativePath")
    }
}

$markdownFiles = Get-ChildItem -LiteralPath $projectRoot -Recurse -Filter '*.md' -File |
    Where-Object {
        # Validation is limited to tracked/formal documentation.  Pytest
        # basetemp directories can contain generated markdown fixtures and
        # must not become part of the stage contract.
        $_.FullName -notmatch '[\\/](node_modules|\.venv|\.next|dist|\.git|\.cache|\.wrangler|\.pytest-temp|\.pytest-of-[^\\/]+|\.npc-pytest|\.tmp-pytest[^\\/]*)[\\/]'
    }

foreach ($file in $markdownFiles) {
    $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8

    $links = [regex]::Matches($content, '\[[^\]]+\]\(([^)#]+)(?:#[^)]+)?\)')
    foreach ($link in $links) {
        $target = $link.Groups[1].Value
        if ($target -notmatch '^(https?://|mailto:)') {
            $resolvedTarget = [System.IO.Path]::GetFullPath(
                (Join-Path $file.DirectoryName $target)
            )
            if (-not (Test-Path -LiteralPath $resolvedTarget -PathType Leaf)) {
                $relativeFile = Get-ProjectRelativePath $projectRoot $file.FullName
                $failures.Add("Broken link in ${relativeFile}: $target")
            }
        }
    }

    $jsonBlocks = [regex]::Matches($content, '(?ms)```json\s*(.*?)\s*```')
    for ($index = 0; $index -lt $jsonBlocks.Count; $index++) {
        try {
            $null = $jsonBlocks[$index].Groups[1].Value | ConvertFrom-Json
        }
        catch {
            $relativeFile = Get-ProjectRelativePath $projectRoot $file.FullName
            $failures.Add("Invalid JSON block $($index + 1) in $relativeFile")
        }
    }
}

$principlePath = Join-Path $projectRoot 'docs/core-principles.md'
if (Test-Path -LiteralPath $principlePath -PathType Leaf) {
    $principleIds = Select-String -LiteralPath $principlePath -Pattern '^### (P-\d+)' -Encoding UTF8 |
        ForEach-Object { $_.Matches.Groups[1].Value }

    if ($principleIds.Count -ne 17) {
        $failures.Add("Expected 17 principles, found $($principleIds.Count)")
    }

    $duplicatePrinciples = $principleIds | Group-Object | Where-Object Count -gt 1
    foreach ($duplicate in $duplicatePrinciples) {
        $failures.Add("Duplicate principle id: $($duplicate.Name)")
    }
}

$coverageRequirements = @{
    'docs/turn-protocol.md' = @(
        'idempotency_key',
        '对过去事件的声称',
        '确定性结果说明'
    )
    'docs/context-memory-specification.md' = @(
        '精确查询',
        '角色认知',
        '语义相似'
    )
    'docs/consequence-specification.md' = @(
        'sourceEventId',
        '信息传播与通缉',
        'refuse_but_offer_alternative'
    )
    'docs/stage-3c-part2-validation-report.md' = @(
        '地窖暗道',
        '重复提交',
        '权威钥匙',
        '玩家公开状态'
    )
}

foreach ($entry in $coverageRequirements.GetEnumerator()) {
    $path = Join-Path $projectRoot $entry.Key
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        continue
    }

    $content = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    foreach ($requiredText in $entry.Value) {
        if (-not $content.Contains($requiredText)) {
            $failures.Add("Missing coverage text '$requiredText' in $($entry.Key)")
        }
    }
}

if ($failures.Count -gt 0) {
    Write-Host 'Stage 0 validation failed:'
    foreach ($failure in $failures) {
        Write-Host "- $failure"
    }
    exit 1
}

Write-Host "Stage 0 validation passed: $($requiredFiles.Count) required files, $($markdownFiles.Count) Markdown files."
