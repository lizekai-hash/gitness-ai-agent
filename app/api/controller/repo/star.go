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

package repo

import (
	"context"
	"fmt"

	"github.com/harness/gitness/app/auth"
	"github.com/harness/gitness/types/enum"
)

// IsStarred checks if the current user has starred/favorited the repository.
func (c *Controller) IsStarred(
	ctx context.Context,
	session *auth.Session,
	repoRef string,
) (bool, error) {
	repoCore, err := c.getRepoCheckAccess(ctx, session, repoRef, enum.PermissionRepoView)
	if err != nil {
		return false, fmt.Errorf("access check failed: %w", err)
	}

	favoritesMap, err := c.favoriteStore.Map(ctx, session.Principal.ID, enum.ResourceTypeRepo, []int64{repoCore.ID})
	if err != nil {
		return false, fmt.Errorf("failed to check if repo is starred: %w", err)
	}

	return favoritesMap[repoCore.ID], nil
}
