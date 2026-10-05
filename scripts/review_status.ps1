<#
.SYNOPSIS
    Report every outstanding Copilot review finding on a pull request.

.DESCRIPTION
    Findings are reported in two places, and reading only one of them hides the rest:

      - inline threads, anchored to changed lines;
      - the review body, which carries the severity counts and the "Previously missed"
        section listing findings in code that has not changed since the last review.

    Filtering by "created after my last push" cannot show an outstanding older finding,
    which is exactly what "previously missed" means, so this script never filters by time.
    It reports what is still open, whenever it was first raised.

    The reviewer account has two logins: `Copilot` in REST and `copilot-pull-request-reviewer`
    in GraphQL. Both are matched here.

.EXAMPLE
    .\scripts\review_status.ps1 -PullRequest 5
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][int]$PullRequest,
    [string]$Repository = "jluqueba/sphereloom"
)

$ErrorActionPreference = "Stop"

$owner, $repo = $Repository.Split("/")

# The review must have been produced for the current head commit. A timestamp comparison is
# not enough: `git commit` records when a commit was created locally, so a commit made
# before a review was submitted but pushed after it would look reviewed. The review object
# carries the sha it examined, so compare identities and skip the clock entirely.
$head = gh api "repos/$Repository/pulls/$PullRequest" --jq '.head.sha'
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($head)) {
    throw "Could not read the head commit of PR $PullRequest."
}
Write-Host "head $($head.Substring(0,7))"

Write-Host ""
Write-Host "== Latest review body ==" -ForegroundColor Cyan

# `--slurp` because `--paginate` otherwise emits one JSON array per page, and
# ConvertFrom-Json rejects concatenated documents. Without it this script dies on exactly
# the long-running pull requests it exists for.
$reviewsRaw = gh api "repos/$Repository/pulls/$PullRequest/reviews" --paginate --slurp
if ($LASTEXITCODE -ne 0) {
    throw "Could not read the reviews of PR $PullRequest."
}
$reviews = $reviewsRaw | ConvertFrom-Json
$latest = $reviews |
    Where-Object { $_.user.login -like "*opilot*" -and $_.body } |
    Select-Object -Last 1

$findingCount = $null
$missedCount = $null
$reviewIsCurrent = $false

if (-not $latest) {
    Write-Host "No review body found yet." -ForegroundColor Yellow
}
else {
    $reviewIsCurrent = ($latest.commit_id -eq $head)
    Write-Host "submitted: $($latest.submitted_at)  for $($latest.commit_id.Substring(0,7))"
    if (-not $reviewIsCurrent) {
        Write-Host "This review examined a different commit: the current code is unreviewed." -ForegroundColor Yellow
    }

    $findings = [regex]::Match($latest.body, '(?m)^\*\*Findings:\*\*\s*(.+?)\s*$')
    if ($findings.Success) {
        $summary = ($findings.Groups[1].Value -replace '<[^>]+>', '').Trim()
        Write-Host "Findings: $summary"
        # "None" is the only value that clears this check. Any digit means the review
        # reported findings, whatever their severity.
        $findingCount = if ($summary -match 'None') { 0 } else { ([regex]::Matches($summary, '\d+') | Measure-Object -Property Value -Sum).Sum }
        if ($null -eq $findingCount) { $findingCount = 1 }
    }
    else {
        Write-Host "Findings: could not be parsed" -ForegroundColor Yellow
    }

    # "Previously missed" is the section most easily overlooked: it is collapsed in the UI
    # and absent from the inline comments entirely.
    $missed = [regex]::Match($latest.body, 'Previously missed \((\d+)\)')
    $missedCount = if ($missed.Success) { [int]$missed.Groups[1].Value } else { 0 }
    if ($missedCount -gt 0) {
        Write-Host "Previously missed: $missedCount" -ForegroundColor Red
        foreach ($m in [regex]::Matches($latest.body, '(?s)<summary><picture>.*?</picture>\s*(.*?)</summary>\s*(.*?)</details>')) {
            $title = $m.Groups[1].Value.Trim()
            $detail = ($m.Groups[2].Value -replace '<[^>]+>', '').Trim()
            if ($title -and $detail) {
                Write-Host "  - $title" -ForegroundColor Red
                Write-Host "    $($detail -replace '\s+', ' ')"
            }
        }
    }
    else {
        Write-Host "Previously missed: none" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "== Unresolved inline threads ==" -ForegroundColor Cyan

$query = @'
query($owner:String!,$repo:String!,$num:Int!,$after:String){
  repository(owner:$owner,name:$repo){
    pullRequest(number:$num){
      reviewThreads(first:100, after:$after){
        pageInfo{ hasNextPage endCursor }
        nodes{ isResolved path line comments(first:1){ nodes{ author{login} createdAt body } } }
      }
    }
  }
}
'@

$queryFile = Join-Path ([System.IO.Path]::GetTempPath()) "sphereloom_review_$PID.graphql"
$query | Out-File -FilePath $queryFile -Encoding utf8

# Paginated, and every page is verified. Treating a failed query as "no threads" would turn
# an unreadable review into a clean one, which is the worst outcome this script can produce:
# $ErrorActionPreference does not apply to native commands, so gh failing is silent.
$threads = @()
$cursor = $null
try {
    do {
        $ghArgs = @("graphql", "-F", "owner=$owner", "-F", "repo=$repo", "-F", "num=$PullRequest", "-F", "query=@$queryFile")
        $ghArgs += if ($cursor) { @("-F", "after=$cursor") } else { @("-F", "after=") }

        $raw = gh api @ghArgs
        if ($LASTEXITCODE -ne 0) {
            throw "The review-thread query failed."
        }

        $parsed = $raw | ConvertFrom-Json
        if ($parsed.errors) {
            throw "The review-thread query returned errors: $($parsed.errors.message -join '; ')"
        }

        $page = $parsed.data.repository.pullRequest.reviewThreads
        if ($null -eq $page) {
            throw "The review-thread query returned no data."
        }

        $threads += $page.nodes
        $cursor = $page.pageInfo.endCursor
    } while ($page.pageInfo.hasNextPage)
}
finally {
    Remove-Item $queryFile -ErrorAction SilentlyContinue
}

$open = @(
    $threads | Where-Object { $_.isResolved -eq $false -and $_.comments.nodes[0].author.login -like "*opilot*" }
)

if ($open.Count -eq 0) {
    Write-Host "None." -ForegroundColor Green
}
else {
    foreach ($thread in $open) {
        Write-Host "  - $($thread.path):$($thread.line)" -ForegroundColor Red
        Write-Host "    $(($thread.comments.nodes[0].body -replace '\s+', ' '))"
    }
}

Write-Host ""

# Every condition must hold, and an unparseable summary counts as not clean: a check that
# cannot read its input must not report success.
$reasons = @()
if (-not $latest) { $reasons += "no review has been posted yet" }
elseif (-not $reviewIsCurrent) { $reasons += "the latest review examined a different commit" }
if ($null -eq $findingCount) { $reasons += "the findings summary could not be parsed" }
elseif ($findingCount -gt 0) { $reasons += "the latest review reported $findingCount finding(s)" }
if ($missedCount -gt 0) { $reasons += "$missedCount previously-missed finding(s) remain" }
if ($open.Count -gt 0) { $reasons += "$($open.Count) unresolved inline thread(s)" }

if ($reasons.Count -eq 0) {
    Write-Host "Review is clean: safe to merge once CI is green." -ForegroundColor Green
    exit 0
}

Write-Host "Review is NOT clean: do not merge." -ForegroundColor Red
foreach ($reason in $reasons) { Write-Host "  - $reason" -ForegroundColor Red }
exit 1
