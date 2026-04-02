// Copyright 2023 Harness, Inc.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package git

import (
	"context"

	"github.com/harness/gitness/git/api"
	"github.com/harness/gitness/git/merge"

	"golang.org/x/sync/errgroup"
)

type SummaryParams struct {
	ReadParams
}

type SummaryOutput struct {
	CommitCount int
	BranchCount int
	TagCount    int
}

func (s *Service) Summary(
	ctx context.Context,
	params SummaryParams,
) (SummaryOutput, error) {
	repoPath := getFullPathForRepo(s.reposRoot, params.RepoUID)

	defaultBranch, err := s.git.GetDefaultBranch(ctx, repoPath)
	if err != nil {
		// if the default branch can't be determined, return empty summary instead of error
		return SummaryOutput{}, nil
	}
	defaultBranchRef := api.GetReferenceFromBranchName(defaultBranch)

	g, ctx := errgroup.WithContext(ctx)

	var commitCount, branchCount, tagCount int

	g.Go(func() error {
		commitCount, _ = merge.CommitCount(ctx, repoPath, "", defaultBranchRef)
		return nil
	})

	g.Go(func() error {
		branchCount, _ = s.git.GetBranchCount(ctx, repoPath)
		return nil
	})

	g.Go(func() error {
		tagCount, _ = s.git.GetTagCount(ctx, repoPath)
		return nil
	})

	_ = g.Wait()

	return SummaryOutput{
		CommitCount: commitCount,
		BranchCount: branchCount,
		TagCount:    tagCount,
	}, nil
}
