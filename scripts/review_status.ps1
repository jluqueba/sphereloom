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

Write-Host "== Latest review body ==" -ForegroundColor Cyan

$reviews = gh api "repos/$Repository/pulls/$PullRequest/reviews" --paginate | ConvertFrom-Json
$latest = $reviews |
    Where-Object { $_.user.login -like "*opilot*" -and $_.body } |
    Select-Object -Last 1

if (-not $latest) {
    Write-Host "No review body found yet." -ForegroundColor Yellow
}
else {
    Write-Host "submitted: $($latest.submitted_at)"

    $findings = [regex]::Match($latest.body, '(?m)^\*\*Findings:\*\*\s*(.+?)\s*$')
    if ($findings.Success) {
        $summary = ($findings.Groups[1].Value -replace '<[^>]+>', '').Trim()
        Write-Host "Findings: $summary"
    }

    # "Previously missed" is the section most easily overlooked: it is collapsed in the UI
    # and absent from the inline comments entirely.
    $missed = [regex]::Match($latest.body, 'Previously missed \((\d+)\)')
    if ($missed.Success -and [int]$missed.Groups[1].Value -gt 0) {
        Write-Host "Previously missed: $($missed.Groups[1].Value)" -ForegroundColor Red
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
query($owner:String!,$repo:String!,$num:Int!){
  repository(owner:$owner,name:$repo){
    pullRequest(number:$num){
      reviewThreads(first:100){
        nodes{ isResolved path line comments(first:1){ nodes{ author{login} createdAt body } } }
      }
    }
  }
}
'@

$owner, $repo = $Repository.Split("/")
$queryFile = Join-Path ([System.IO.Path]::GetTempPath()) "sphereloom_review_$PID.graphql"
$query | Out-File -FilePath $queryFile -Encoding utf8
try {
    $result = gh api graphql -F owner=$owner -F repo=$repo -F num=$PullRequest -F query=@$queryFile | ConvertFrom-Json
}
finally {
    Remove-Item $queryFile -ErrorAction SilentlyContinue
}

$open = @(
    $result.data.repository.pullRequest.reviewThreads.nodes |
        Where-Object { $_.isResolved -eq $false -and $_.comments.nodes[0].author.login -like "*opilot*" }
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
$clean = ($open.Count -eq 0) -and (-not ($missed.Success -and [int]$missed.Groups[1].Value -gt 0))
if ($clean) {
    Write-Host "Review is clean: safe to merge once CI is green." -ForegroundColor Green
    exit 0
}

Write-Host "Review is NOT clean: do not merge." -ForegroundColor Red
exit 1
